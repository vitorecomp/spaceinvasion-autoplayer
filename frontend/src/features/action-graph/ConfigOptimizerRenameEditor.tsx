import React from "react";

export interface ConfigOptimizerRenameEditorProps {
  planetId: number;
  coords: string;
  gameName: string;
  renameDraft: string;
  renameError: string | null;
  isSavingThis: boolean;
  isSyncingNames: boolean;
  onDraftChange: (value: string) => void;
  onSubmitRename: (planetId: number, rawName: string, fallbackGameName?: string) => void;
  onSyncSingleName: (planetId: number, rawName: string) => void;
  onCancel: () => void;
}

export const ConfigOptimizerRenameEditor: React.FC<ConfigOptimizerRenameEditorProps> = ({
  planetId,
  coords,
  gameName,
  renameDraft,
  renameError,
  isSavingThis,
  isSyncingNames,
  onDraftChange,
  onSubmitRename,
  onSyncSingleName,
  onCancel,
}) => {
  return (
    <div className="ag-co-rename-form" onClick={(e) => e.stopPropagation()}>
      <input
        type="text"
        id={`co-rename-input-${planetId}`}
        className="ag-co-rename-input"
        aria-label={`New name for planet ${coords}`}
        maxLength={64}
        value={renameDraft}
        placeholder={gameName || coords}
        autoFocus
        disabled={isSavingThis}
        onChange={(e) => onDraftChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onSubmitRename(planetId, renameDraft, gameName);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
      />
      <div className="ag-co-rename-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          id={`btn-co-rename-save-${planetId}`}
          disabled={isSavingThis || isSyncingNames}
          onClick={() => onSubmitRename(planetId, renameDraft, gameName)}
        >
          {isSavingThis ? "Saving..." : "Save"}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id={`btn-co-rename-sync-${planetId}`}
          title="Save custom name and immediately update the planet name inside SpaceInvasion Classic"
          disabled={isSavingThis || isSyncingNames}
          onClick={() => onSyncSingleName(planetId, renameDraft)}
        >
          {isSyncingNames ? "Syncing..." : "Save & Sync"}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id={`btn-co-rename-reset-${planetId}`}
          title="Clear custom name and restore in-game planet name"
          disabled={isSavingThis || isSyncingNames}
          onClick={() => onSubmitRename(planetId, "", gameName)}
        >
          Reset
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id={`btn-co-rename-cancel-${planetId}`}
          disabled={isSavingThis || isSyncingNames}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      {renameError ? (
        <div className="ag-co-rename-error" id={`co-rename-error-${planetId}`} role="alert">
          {renameError}
        </div>
      ) : null}
    </div>
  );
};
