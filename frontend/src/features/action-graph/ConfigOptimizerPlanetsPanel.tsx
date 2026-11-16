import React from "react";
import { SortableTh } from "../../components/SortableTh";
import { TablePagination } from "../../components/TablePagination";
import type { SortDirection } from "../../hooks/useTableSort";
import { fmtCompact } from "../../utils/formatters";
import {
  type NormalizedPlanetRow,
  type OwnedDashboardPlanetRow,
  resolveLockedSpecForRow,
} from "./configOptimizerDraft";
import { ConfigOptimizerRenameEditor } from "./ConfigOptimizerRenameEditor";
import {
  BUILDING_NAMES,
  BUILDING_SHORT_NAMES,
  KEY_PLANET_BUILDINGS,
  PLANET_SPECIALIZATION_OPTIONS,
  formatSpecializationLabel,
} from "./plannerHelpers";

export type PlanetSortKey = "planet" | "spec" | "temp" | "fields" | "time" | "output";

export interface ConfigOptimizerPlanetsPanelProps {
  hidden: boolean;
  isJobRunning: boolean;
  isSubmitting: boolean;
  isSyncingNames: boolean;
  lockedSpecsCount: number;
  stickyPlanApplied: boolean;
  rolesPreservedCount: number;
  rolesChangedCount: number;
  unsyncedNameCount: number;
  planetRows: NormalizedPlanetRow[];
  pagedPlanets: NormalizedPlanetRow[];
  sortedPlanetCount: number;
  planetPage: number;
  planetPageSize: number;
  sortKey: PlanetSortKey | null;
  sortDirection: SortDirection;
  onRequestSort: (key: PlanetSortKey) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  ownedDashboardPlanets: OwnedDashboardPlanetRow[];
  selectedPlanetDetail: NormalizedPlanetRow | null;
  planetSpecializations: Record<string, string>;
  editingPlanetId: number | null;
  renameDraft: string;
  renamingPlanetId: number | null;
  renameError: string | null;
  onRenameDraftChange: (value: string) => void;
  onStartRename: (planetId: number, currentName: string) => void;
  onCancelRename: () => void;
  onSubmitRename: (planetId: number, rawName: string, fallbackGameName?: string) => void;
  onSyncSinglePlanetName: (planetId: number, rawName: string) => void;
  onSyncAllPlanetNames: () => void;
  onLockAllCurrentRoles: () => void;
  onUnlockAllRoles: () => void;
  onPlanetSpecChange: (row: NormalizedPlanetRow, rawValue: string) => void;
  onSelectPlanet: (slot: number) => void;
  onOpenPlanetModal: (slot: number) => void;
}

export const ConfigOptimizerPlanetsPanel: React.FC<ConfigOptimizerPlanetsPanelProps> = ({
  hidden,
  isJobRunning,
  isSubmitting,
  isSyncingNames,
  lockedSpecsCount,
  stickyPlanApplied,
  rolesPreservedCount,
  rolesChangedCount,
  unsyncedNameCount,
  planetRows,
  pagedPlanets,
  sortedPlanetCount,
  planetPage,
  planetPageSize,
  sortKey,
  sortDirection,
  onRequestSort,
  onPageChange,
  onPageSizeChange,
  ownedDashboardPlanets,
  selectedPlanetDetail,
  planetSpecializations,
  editingPlanetId,
  renameDraft,
  renamingPlanetId,
  renameError,
  onRenameDraftChange,
  onStartRename,
  onCancelRename,
  onSubmitRename,
  onSyncSinglePlanetName,
  onSyncAllPlanetNames,
  onLockAllCurrentRoles,
  onUnlockAllRoles,
  onPlanetSpecChange,
  onSelectPlanet,
  onOpenPlanetModal,
}) => {
  return (
    <div className="ag-co-content" hidden={hidden}>
      <div className="ag-co-spec-bar" id="co-spec-toolbar">
        <div className="ag-co-spec-summary tabular">
          <span className={`ag-co-spec-badge${lockedSpecsCount > 0 ? " is-locked" : ""}`}>
            {`Role Locks: ${lockedSpecsCount}/${planetRows.length}`}
          </span>
          {stickyPlanApplied ? (
            <span className="ag-co-spec-badge is-sticky">
              {`Sticky Plan Kept (${rolesPreservedCount} role${rolesPreservedCount === 1 ? "" : "s"} preserved)`}
            </span>
          ) : rolesChangedCount > 0 ? (
            <span className="ag-co-spec-badge is-changed">
              {`${rolesChangedCount} role${rolesChangedCount === 1 ? "" : "s"} switched`}
            </span>
          ) : (
            <span className="ag-toolbar-meta">
              Lock any colony role below to guarantee it never flips on future optimizer runs.
            </span>
          )}
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className={`btn ${unsyncedNameCount > 0 ? "btn-primary" : "btn-secondary"} btn-sm`}
            id="btn-co-sync-planet-names"
            disabled={isSyncingNames || isSubmitting}
            title="Update planet names inside SpaceInvasion Classic (internalSettings) to match Config Optimizer names"
            onClick={onSyncAllPlanetNames}
          >
            {isSyncingNames
              ? "Syncing Names..."
              : unsyncedNameCount > 0
                ? `Sync Names to Game (${unsyncedNameCount})`
                : "Sync Names to Game"}
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-co-lock-all"
            disabled={planetRows.length === 0 || isSubmitting}
            onClick={onLockAllCurrentRoles}
          >
            Lock Current Roles
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-co-unlock-all"
            disabled={lockedSpecsCount === 0 || isSubmitting}
            onClick={onUnlockAllRoles}
          >
            Unlock All
          </button>
        </div>
      </div>

      <div className="table-wrap table-scroll ag-co-planets-table-wrap">
        <table id="co-planets-table">
          <thead>
            <tr>
              <SortableTh
                label="Planet"
                sortKey="planet"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
              />
              <SortableTh
                label="Specialization"
                sortKey="spec"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
              />
              <SortableTh
                label="Temp"
                sortKey="temp"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <SortableTh
                label="Fields Used / Max"
                sortKey="fields"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <th>Built → Target (Key Buildings)</th>
              <th>Warehouse Driver</th>
              <SortableTh
                label="Last-Lvl Time"
                sortKey="time"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <SortableTh
                label="Monthly Output"
                sortKey="output"
                activeKey={sortKey}
                direction={sortDirection}
                onSort={onRequestSort}
                align="right"
              />
              <th>Reserved Floors</th>
            </tr>
          </thead>
          <tbody id="co-planets-tbody">
            {pagedPlanets.length === 0 ? (
              <tr>
                <td colSpan={9} className="empty-cell">
                  {isJobRunning
                    ? "Optimizing planetary building targets..."
                    : "No Config Optimization run yet. Adjust parameters on the right and click Run Config Optimizer."}
                </td>
              </tr>
            ) : (
              pagedPlanets.map((row) => {
                const isSel = selectedPlanetDetail?.slot === row.slot;
                const keyPills = KEY_PLANET_BUILDINGS.filter(
                  (bk) => (row.targetLevels[bk] ?? 0) > 0 || (row.builtLevels[bk] ?? 0) > 0
                ).slice(0, 8);
                const resEntries = Object.entries(row.reservedFloors);
                const currentLock = resolveLockedSpecForRow(row, planetSpecializations);
                const isLocked = currentLock !== "auto";
                const canRename = !row.isPlaceholder && row.planetId > 0;
                const isEditingName = canRename && editingPlanetId === row.planetId;
                return (
                  <tr
                    key={`${row.slot}-${row.coords}`}
                    data-co-planet-slot={String(row.slot)}
                    className={`clickable-row${isSel ? " row-selected" : ""}`}
                    title="Click to configure desired target buildings and name for this planet"
                    onClick={() => {
                      onSelectPlanet(row.slot);
                      onOpenPlanetModal(row.slot);
                    }}
                  >
                    <td className="ag-co-planet-cell">
                      {isEditingName ? (
                        <ConfigOptimizerRenameEditor
                          planetId={row.planetId}
                          coords={row.coords}
                          gameName={row.gameName}
                          renameDraft={renameDraft}
                          renameError={renameError}
                          isSavingThis={renamingPlanetId === row.planetId}
                          isSyncingNames={isSyncingNames}
                          onDraftChange={onRenameDraftChange}
                          onSubmitRename={onSubmitRename}
                          onSyncSingleName={onSyncSinglePlanetName}
                          onCancel={onCancelRename}
                        />
                      ) : (
                        <>
                          <div className="ag-co-planet-head">
                            <div className="action-title tabular">
                              {`#${row.slot} ${row.name}`}
                              {row.isCentral ? " ★" : ""}
                            </div>
                          </div>
                          <div className="action-sub tabular">
                            {`[${row.coords}]${row.isPlaceholder ? " · PREVIEW" : ""}${row.hasCustomName && row.gameName && row.gameName !== row.name ? ` · Unsynced (Game: ${row.gameName})` : ""}`}
                          </div>
                          <div className="ag-co-planet-row-btns">
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm ag-co-rename-trigger"
                              id={`btn-co-edit-planet-${row.slot}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                onSelectPlanet(row.slot);
                                onOpenPlanetModal(row.slot);
                              }}
                            >
                              Configure
                            </button>
                            {canRename ? (
                              <button
                                type="button"
                                className="btn btn-secondary btn-sm ag-co-rename-trigger"
                                id={`btn-co-rename-${row.planetId}`}
                                data-co-rename-planet={String(row.planetId)}
                                aria-label={`Rename ${row.name} [${row.coords}]`}
                                disabled={renamingPlanetId !== null}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onStartRename(row.planetId, row.name);
                                }}
                              >
                                Rename
                              </button>
                            ) : null}
                          </div>
                        </>
                      )}
                    </td>
                    <td className="ag-co-spec-cell" onClick={(e) => e.stopPropagation()}>
                      <div className="ag-co-spec-tags">
                        <span
                          className={`ag-co-spec-badge tabular${isLocked ? " is-locked" : ""}${row.specializationChanged ? " is-changed" : ""}`}
                          title={
                            row.previousSpecialization && row.specializationChanged
                              ? `Switched from ${formatSpecializationLabel(row.previousSpecialization)} to ${formatSpecializationLabel(row.detectedSpecialization)}`
                              : `Current target specialization: ${formatSpecializationLabel(row.detectedSpecialization)}`
                          }
                        >
                          {formatSpecializationLabel(row.detectedSpecialization)}
                          {isLocked ? " · LOCKED" : ""}
                        </span>
                        {row.specializationChanged && row.previousSpecialization ? (
                          <span className="action-sub tabular">
                            {`was ${formatSpecializationLabel(row.previousSpecialization)}`}
                          </span>
                        ) : null}
                      </div>
                      <select
                        id={`co-spec-select-${row.slot}`}
                        className="ag-co-spec-select"
                        aria-label={`Specialization lock for ${row.name}`}
                        value={currentLock}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          e.stopPropagation();
                          onPlanetSpecChange(row, e.target.value);
                        }}
                      >
                        {PLANET_SPECIALIZATION_OPTIONS.map((opt) => (
                          <option key={opt.key} value={opt.key}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="col-right tabular">
                      {row.temperature !== null ? `${row.temperature}°C` : "—"}
                    </td>
                    <td className="col-right tabular">
                      {`${row.fieldsUsedCurrent ?? "—"}→${row.fieldsUsedTarget}/${row.fieldsMax}`}
                    </td>
                    <td>
                      <div className="ag-floor-pills">
                        {keyPills.map((bk) => {
                          const b = row.builtLevels[bk] ?? 0;
                          const t = row.targetLevels[bk] ?? b;
                          const delta = t - b;
                          return (
                            <span
                              key={bk}
                              className={`ag-mini-chip tabular${delta > 0 ? " is-upgrade" : delta < 0 ? " is-demolish" : ""}`}
                              title={`${BUILDING_NAMES[bk] || bk}: built L${b}, target L${t}${delta < 0 ? " (demolished only when colony runs out of fields)" : ""}`}
                            >
                              {`${BUILDING_SHORT_NAMES[bk] || bk} ${b}→${t}`}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td className="tabular">{row.warehouseDriver}</td>
                    <td className="col-right tabular">{row.lastLevelTimeText}</td>
                    <td className="col-right tabular">
                      {row.monthlyShips !== null
                        ? `${row.monthlyShips.toFixed(2)}/mo`
                        : row.monthlySpiceEq !== null
                          ? `${fmtCompact(row.monthlySpiceEq)} SpEq`
                          : "—"}
                    </td>
                    <td>
                      {resEntries.length === 0 ? (
                        <span className="action-sub">Built floor</span>
                      ) : (
                        <div className="ag-floor-pills">
                          {resEntries.map(([bk, lvl]) => (
                            <span key={bk} className="ag-mini-chip is-reserved tabular">
                              {`${bk} ≥ ${lvl}`}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <TablePagination
        idPrefix="co-planets"
        totalItems={sortedPlanetCount}
        page={planetPage}
        pageSize={planetPageSize}
        itemLabel="planets"
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />

      {planetRows.length === 0 && ownedDashboardPlanets.length > 0 ? (
        <div className="ag-co-pre-run-planets" id="co-owned-planets-rename">
          <div className="ag-co-detail-head tabular">
            <strong>Owned Planets — Configure &amp; Sync Names</strong>
            <div className="inline-actions">
              <span className="ag-toolbar-meta">
                Click any colony to set target buildings or sync custom names to the game.
              </span>
              <button
                type="button"
                className={`btn ${unsyncedNameCount > 0 ? "btn-primary" : "btn-secondary"} btn-sm`}
                id="btn-co-sync-planet-names-pre"
                disabled={isSyncingNames}
                onClick={onSyncAllPlanetNames}
              >
                {isSyncingNames
                  ? "Syncing Names..."
                  : unsyncedNameCount > 0
                    ? `Sync Names to Game (${unsyncedNameCount})`
                    : "Sync Names to Game"}
              </button>
            </div>
          </div>
          <div className="ag-co-pre-run-grid">
            {ownedDashboardPlanets.map((pl) => {
              const isEditingName = editingPlanetId === pl.planetId;
              return (
                <div
                  key={pl.planetId}
                  className="ag-co-pre-run-card clickable-row"
                  onClick={() => onOpenPlanetModal(pl.slot)}
                >
                  {isEditingName ? (
                    <ConfigOptimizerRenameEditor
                      planetId={pl.planetId}
                      coords={pl.coords}
                      gameName={pl.gameName}
                      renameDraft={renameDraft}
                      renameError={renameError}
                      isSavingThis={renamingPlanetId === pl.planetId}
                      isSyncingNames={isSyncingNames}
                      onDraftChange={onRenameDraftChange}
                      onSubmitRename={onSubmitRename}
                      onSyncSingleName={onSyncSinglePlanetName}
                      onCancel={onCancelRename}
                    />
                  ) : (
                    <div className="ag-co-planet-head">
                      <div className="action-title tabular">{`#${pl.slot} ${pl.name}`}</div>
                      <div className="ag-co-planet-row-btns">
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm ag-co-rename-trigger"
                          id={`btn-co-edit-planet-${pl.slot}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenPlanetModal(pl.slot);
                          }}
                        >
                          Configure
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm ag-co-rename-trigger"
                          id={`btn-co-rename-${pl.planetId}`}
                          data-co-rename-planet={String(pl.planetId)}
                          aria-label={`Rename ${pl.name} [${pl.coords}]`}
                          disabled={renamingPlanetId !== null}
                          onClick={(e) => {
                            e.stopPropagation();
                            onStartRename(pl.planetId, pl.name);
                          }}
                        >
                          Rename
                        </button>
                      </div>
                    </div>
                  )}
                  <div className="action-sub tabular">
                    {`[${pl.coords}]${pl.hasCustomName && pl.gameName && pl.gameName !== pl.name ? ` · Unsynced (Game: ${pl.gameName})` : ""}`}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {selectedPlanetDetail ? (
        <div className="ag-co-detail-strip" id="co-planet-detail">
          <div className="ag-co-detail-head tabular">
            <strong>
              {`Slot #${selectedPlanetDetail.slot} · ${selectedPlanetDetail.name} [${selectedPlanetDetail.coords}]`}
            </strong>
            <div className="inline-actions">
              <span>
                {`Role: ${formatSpecializationLabel(selectedPlanetDetail.detectedSpecialization)} · Fields: ${selectedPlanetDetail.fieldsUsedTarget}/${selectedPlanetDetail.fieldsMax}${selectedPlanetDetail.levelsToDemolish > 0 ? ` · Off-role drop: -${selectedPlanetDetail.levelsToDemolish} (${selectedPlanetDetail.demolitionsWhenFull} when out of fields)` : ""}`}
              </span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                id="btn-co-detail-edit-planet"
                onClick={() => onOpenPlanetModal(selectedPlanetDetail.slot)}
              >
                Edit Planet Config
              </button>
            </div>
          </div>
          <div className="ag-co-detail-grid">
            {selectedPlanetDetail.buildingsDetail.map((bd) => (
              <div
                key={bd.key}
                className={`ag-co-bld-cell tabular${bd.toBuild > 0 ? " is-upgrade" : bd.toDemolish > 0 ? " is-demolish" : ""}${bd.reserved > 0 ? " is-reserved" : ""}`}
              >
                <span className="ag-co-bld-name">{`${bd.key} ${BUILDING_SHORT_NAMES[bd.key] || bd.name}`}</span>
                <strong className="ag-co-bld-vals">
                  {`${bd.built} → ${bd.target}${bd.toBuild > 0 ? ` (+${bd.toBuild})` : bd.toDemolish > 0 ? ` (-${bd.toDemolish})` : ""}`}
                </strong>
                {bd.toDemolish > 0 ? (
                  <span className="ag-co-bld-res">Demolish @ full fields</span>
                ) : null}
                {bd.reserved > 0 ? (
                  <span className="ag-co-bld-res">{`Floor ≥ ${bd.reserved}`}</span>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};
