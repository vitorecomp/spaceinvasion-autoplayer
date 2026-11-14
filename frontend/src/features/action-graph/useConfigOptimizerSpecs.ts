import { useCallback, useState, type Dispatch, type SetStateAction } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import type {
  ConfigOptimizerGetPayload,
  ConfigOptimizerYearlyProjection,
  FeedbackKind,
} from "../../types/api";
import {
  type NormalizedPlanetRow,
  planetSpecKeyForRow,
  sanitizeSpecializationsMap,
} from "./configOptimizerDraft";
import {
  type ConfigOptimizerDraft,
  formatSpecializationLabel,
  normalizeSpecializationKey,
} from "./plannerHelpers";

export interface UseConfigOptimizerSpecsArgs {
  apiOnline: boolean;
  draft: ConfigOptimizerDraft;
  planetRows: NormalizedPlanetRow[];
  apiCommand: <T = any>(payload: Record<string, any>) => Promise<T>;
  setDraft: Dispatch<SetStateAction<ConfigOptimizerDraft>>;
  setDraftDirty: Dispatch<SetStateAction<boolean>>;
  setOptData: Dispatch<SetStateAction<ConfigOptimizerGetPayload | null>>;
  showFeedback: (message: string, kind?: FeedbackKind, opts?: { autoHideMs?: number }) => void;
}

export function useConfigOptimizerSpecs({
  apiOnline,
  draft,
  planetRows,
  apiCommand,
  setDraft,
  setDraftDirty,
  setOptData,
  showFeedback,
}: UseConfigOptimizerSpecsArgs) {
  const [yearlyGraphOpen, setYearlyGraphOpen] = useState<boolean>(false);
  const [yearlyHorizon, setYearlyHorizon] = useState<number>(5);
  const [yearlyProjection, setYearlyProjection] =
    useState<ConfigOptimizerYearlyProjection | null>(null);
  const [yearlyLoading, setYearlyLoading] = useState<boolean>(false);
  const [yearlyError, setYearlyError] = useState<string | null>(null);

  const persistSpecializations = async (nextSpecs: Record<string, string>, successMsg: string) => {
    const cleaned = sanitizeSpecializationsMap(nextSpecs);
    setDraft((prev) => ({ ...prev, planet_specializations: cleaned }));
    setDraftDirty(true);
    if (!apiOnline) {
      showFeedback(`${successMsg} (staged locally; will apply on next run).`, "ok");
      return;
    }
    try {
      const res = await apiCommand({
        command: "config_optimizer_set_specializations",
        planet_specializations: cleaned,
        replace: true,
      });
      const persisted = sanitizeSpecializationsMap(res?.planet_specializations ?? cleaned);
      setDraft((prev) => ({ ...prev, planet_specializations: persisted }));
      setOptData((prev) =>
        prev
          ? {
              ...prev,
              planet_specializations: persisted,
              active: prev.active
                ? {
                    ...prev.active,
                    params: { ...(prev.active.params || {}), planet_specializations: persisted },
                  }
                : prev.active,
            }
          : prev
      );
      showFeedback(successMsg, "ok");
    } catch (err: any) {
      showFeedback(
        `Specialization lock staged locally (API save failed: ${err.message}).`,
        feedbackKindForError(err)
      );
    }
  };

  const onPlanetSpecChange = async (row: NormalizedPlanetRow, rawValue: string) => {
    const nextRole = normalizeSpecializationKey(rawValue);
    const nextSpecs: Record<string, string> = { ...draft.planet_specializations };
    if (row.planetId > 0) delete nextSpecs[String(row.planetId)];
    if (row.coords) delete nextSpecs[row.coords];
    delete nextSpecs[`slot:${row.slot}`];
    if (nextRole !== "auto") nextSpecs[planetSpecKeyForRow(row)] = nextRole;
    const label = formatSpecializationLabel(nextRole);
    await persistSpecializations(
      nextSpecs,
      nextRole === "auto"
        ? `Unlocked ${row.name} [${row.coords}] to Auto specialization.`
        : `Locked ${row.name} [${row.coords}] specialization to ${label}. Click Run Config Optimizer to re-solve.`
    );
  };

  const onLockAllCurrentRoles = async () => {
    const nextSpecs: Record<string, string> = {};
    let locked = 0;
    for (const row of planetRows) {
      const detected = normalizeSpecializationKey(row.detectedSpecialization);
      if (detected !== "auto") {
        nextSpecs[planetSpecKeyForRow(row)] = detected;
        locked += 1;
      }
    }
    if (locked === 0) {
      showFeedback(
        "No detected planet specializations to lock yet — run Config Optimizer first.",
        "warn"
      );
      return;
    }
    await persistSpecializations(
      nextSpecs,
      `Locked ${locked} planet specialization${locked === 1 ? "" : "s"} to current roles.`
    );
  };

  const onUnlockAllRoles = async () => {
    await persistSpecializations({}, "Unlocked all planet specializations back to Auto.");
  };

  const fetchYearlyProjection = useCallback(
    async (years: number) => {
      setYearlyLoading(true);
      setYearlyError(null);
      try {
        const res = await apiCommand<{
          ok?: boolean;
          projection?: ConfigOptimizerYearlyProjection;
        }>({
          command: "config_optimizer_yearly_projection",
          horizon_years: years,
        });
        if (res?.projection) setYearlyProjection(res.projection);
      } catch (err: any) {
        setYearlyError(String(err?.message || "Failed to compute yearly projection."));
      } finally {
        setYearlyLoading(false);
      }
    },
    [apiCommand]
  );

  const onOpenYearlyGraph = () => {
    setYearlyGraphOpen(true);
    void fetchYearlyProjection(yearlyHorizon);
  };

  const onChangeYearlyHorizon = (years: number) => {
    setYearlyHorizon(years);
    void fetchYearlyProjection(years);
  };

  return {
    yearlyGraphOpen,
    setYearlyGraphOpen,
    yearlyHorizon,
    yearlyProjection,
    setYearlyProjection,
    yearlyLoading,
    yearlyError,
    onPlanetSpecChange,
    onLockAllCurrentRoles,
    onUnlockAllRoles,
    onOpenYearlyGraph,
    onChangeYearlyHorizon,
  };
}
