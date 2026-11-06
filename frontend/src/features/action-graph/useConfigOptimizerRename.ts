import { useState, type Dispatch, type SetStateAction } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import type { ConfigOptimizerGetPayload, DashboardPayload, FeedbackKind } from "../../types/api";
import {
  type RenamePlanetApiResponse,
  sanitizeCustomNamesMap,
  validatePlanetRename,
} from "./configOptimizerDraft";
import { patchDashboardAfterRename, patchOptDataAfterRename } from "./configOptimizerPlanets";

export interface UseConfigOptimizerRenameArgs {
  customPlanetNames: Record<string, string>;
  apiCommand: <T = any>(payload: Record<string, any>) => Promise<T>;
  setOptData: Dispatch<SetStateAction<ConfigOptimizerGetPayload | null>>;
  setDashboard: Dispatch<SetStateAction<DashboardPayload | null>>;
  fetchOptimizerState: (opts?: { silent?: boolean }) => Promise<void>;
  loadDashboard: (opts?: {
    refresh?: boolean;
    preserveConfig?: boolean;
    force?: boolean;
  }) => Promise<boolean>;
  showFeedback: (message: string, kind?: FeedbackKind, opts?: { autoHideMs?: number }) => void;
}

export function useConfigOptimizerRename({
  customPlanetNames,
  apiCommand,
  setOptData,
  setDashboard,
  fetchOptimizerState,
  loadDashboard,
  showFeedback,
}: UseConfigOptimizerRenameArgs) {
  const [editingPlanetId, setEditingPlanetId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState<string>("");
  const [renamingPlanetId, setRenamingPlanetId] = useState<number | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [isSyncingNames, setIsSyncingNames] = useState<boolean>(false);

  const startRenamingPlanet = (planetId: number, currentName: string) => {
    setEditingPlanetId(planetId);
    setRenameDraft(currentName);
    setRenameError(null);
  };

  const cancelRenamingPlanet = () => {
    setEditingPlanetId(null);
    setRenameDraft("");
    setRenameError(null);
  };

  const onRenameDraftChange = (val: string) => {
    setRenameDraft(val);
    if (renameError) setRenameError(null);
  };

  const submitPlanetRename = async (
    planetId: number,
    rawName: string,
    fallbackGameName?: string
  ) => {
    if (planetId <= 0) return;
    const { trimmed, error } = validatePlanetRename(rawName);
    if (error) {
      setRenameError(error);
      return;
    }
    setRenamingPlanetId(planetId);
    setRenameError(null);
    try {
      const res = await apiCommand<RenamePlanetApiResponse>({
        command: "rename_planet",
        planet_id: planetId,
        name: trimmed,
      });
      const nextCustomNames: Record<string, string> =
        res?.custom_planet_names && typeof res.custom_planet_names === "object"
          ? sanitizeCustomNamesMap(res.custom_planet_names)
          : (() => {
              const copy = { ...customPlanetNames };
              if (trimmed) copy[String(planetId)] = trimmed;
              else delete copy[String(planetId)];
              return copy;
            })();
      const cleared = Boolean(res?.cleared) || !trimmed;
      const resolvedGameName = String(
        res?.game_name || res?.planet?.game_name || fallbackGameName || ""
      ).trim();
      const resolvedDisplayName = String(
        res?.planet?.name ||
          (!cleared && res?.name ? res.name : "") ||
          trimmed ||
          resolvedGameName ||
          `Planet ${planetId}`
      ).trim();

      setOptData((prev) =>
        patchOptDataAfterRename(
          prev,
          planetId,
          resolvedDisplayName,
          resolvedGameName,
          nextCustomNames
        )
      );
      setDashboard((prev) =>
        patchDashboardAfterRename(
          prev,
          planetId,
          resolvedDisplayName,
          resolvedGameName,
          trimmed,
          cleared,
          nextCustomNames
        )
      );
      setEditingPlanetId(null);
      setRenameDraft("");
      setRenameError(null);
      showFeedback(
        String(
          res?.message ||
            (cleared
              ? `Reset planet name to "${resolvedDisplayName}".`
              : `Renamed planet to "${resolvedDisplayName}".`)
        ),
        "ok"
      );
      await Promise.all([
        fetchOptimizerState({ silent: true }),
        loadDashboard({ refresh: false, preserveConfig: true, force: true }),
      ]);
    } catch (err: any) {
      const msg = String(err?.message || "Failed to rename planet.");
      setRenameError(msg);
      showFeedback(`Planet rename failed: ${msg}`, feedbackKindForError(err));
    } finally {
      setRenamingPlanetId(null);
    }
  };

  const onSyncAllPlanetNames = async () => {
    setIsSyncingNames(true);
    try {
      const res = await apiCommand<Record<string, any>>({ command: "sync_planet_names" });
      showFeedback(
        String(
          res?.message || `Synced ${Number(res?.synced_count ?? 0)} planet name(s) in-game.`
        ),
        "ok"
      );
      await Promise.all([
        fetchOptimizerState({ silent: true }),
        loadDashboard({ refresh: false, preserveConfig: true, force: true }),
      ]);
    } catch (err: any) {
      showFeedback(
        `In-game planet name sync failed: ${err.message}`,
        feedbackKindForError(err)
      );
    } finally {
      setIsSyncingNames(false);
    }
  };

  const onSyncSinglePlanetName = async (planetId: number, rawName: string) => {
    if (planetId <= 0) return;
    const { trimmed, error } = validatePlanetRename(rawName);
    if (error) {
      setRenameError(error);
      showFeedback(error, "warn");
      return;
    }
    if (!trimmed) {
      showFeedback("Enter a non-empty planet name to sync in-game.", "warn");
      return;
    }
    setIsSyncingNames(true);
    setRenameError(null);
    try {
      const res = await apiCommand<Record<string, any>>({
        command: "sync_planet_names",
        planet_id: planetId,
        name: trimmed,
      });
      setEditingPlanetId(null);
      setRenameDraft("");
      showFeedback(
        String(res?.message || `Synced "${trimmed}" in-game via internalSettings.`),
        "ok"
      );
      await Promise.all([
        fetchOptimizerState({ silent: true }),
        loadDashboard({ refresh: false, preserveConfig: true, force: true }),
      ]);
    } catch (err: any) {
      const msg = String(err?.message || "Failed to sync planet name in-game.");
      setRenameError(msg);
      showFeedback(`In-game planet name sync failed: ${msg}`, feedbackKindForError(err));
    } finally {
      setIsSyncingNames(false);
    }
  };

  return {
    editingPlanetId,
    renameDraft,
    renamingPlanetId,
    renameError,
    isSyncingNames,
    onRenameDraftChange,
    startRenamingPlanet,
    cancelRenamingPlanet,
    submitPlanetRename,
    onSyncAllPlanetNames,
    onSyncSinglePlanetName,
  };
}
