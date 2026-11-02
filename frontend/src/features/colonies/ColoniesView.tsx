import React, { useEffect, useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import {
  useActivePage,
  useActivePlanet,
  useColonyData,
  useConsoleActions,
  useDashboard,
  useUiPrefs,
} from "../../store";
import type {
  ColonyAttemptCoordStats,
  ColonyAttemptItem,
  ColonyAttemptsSummary,
} from "../../types/api";
import { finiteNum } from "../../utils/formatters";
import { ColoniesAttemptsTable } from "./ColoniesAttemptsTable";
import { ColoniesCandidatesTable } from "./ColoniesCandidatesTable";
import { ColoniesKpiBar } from "./ColoniesKpiBar";
import { attemptOutcomeFor } from "./coloniesHelpers";

export { attemptOutcomeFor } from "./coloniesHelpers";
export type { AttemptOutcome } from "./coloniesHelpers";

export interface ColoniesViewProps {
  hidden?: boolean;
}

export const ColoniesView: React.FC<ColoniesViewProps> = ({ hidden }) => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const activePlanet = useActivePlanet();
  const colonyData = useColonyData();
  const uiPrefs = useUiPrefs();
  const {
    refreshColoniesView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
  } = useConsoleActions();

  const isHidden = hidden ?? activePage !== "colonies";
  const colFilter = uiPrefs.colonyFilter || "all";

  const [minFieldsInput, setMinFieldsInput] = useState<string>("300");
  const [allowColonizeInput, setAllowColonizeInput] = useState<boolean>(false);

  useEffect(() => {
    const cfgMin = colonyData?.min_colony_fields ?? dashboard?.config?.min_colony_fields;
    if (cfgMin != null) {
      setMinFieldsInput(String(cfgMin));
    }
    const cfgAllow = colonyData?.allow_colonize ?? dashboard?.config?.allow_colonize;
    if (cfgAllow != null) {
      setAllowColonizeInput(Boolean(cfgAllow));
    }
  }, [
    colonyData?.allow_colonize,
    colonyData?.min_colony_fields,
    dashboard?.config?.allow_colonize,
    dashboard?.config?.min_colony_fields,
  ]);

  const gc = colonyData?.grade_counts || {};
  const allCands: Array<Record<string, any>> = useMemo(
    () => (Array.isArray(colonyData?.candidates) ? colonyData!.candidates : []),
    [colonyData]
  );

  const minFieldsTarget = Math.max(
    1,
    Math.round(
      finiteNum(colonyData?.min_colony_fields ?? dashboard?.config?.min_colony_fields, 300)
    )
  );
  const attempts: ColonyAttemptItem[] = useMemo(
    () =>
      Array.isArray(colonyData?.colony_attempts)
        ? (colonyData!.colony_attempts as ColonyAttemptItem[]).filter(
            (a) => a && typeof a === "object"
          )
        : [],
    [colonyData]
  );
  const attemptsSummary: ColonyAttemptsSummary = useMemo(
    () =>
      colonyData?.colony_attempts_summary && typeof colonyData.colony_attempts_summary === "object"
        ? (colonyData.colony_attempts_summary as ColonyAttemptsSummary)
        : {},
    [colonyData]
  );
  const attemptsByCoords: Record<string, ColonyAttemptCoordStats> = useMemo(
    () =>
      colonyData?.attempts_by_coords && typeof colonyData.attempts_by_coords === "object"
        ? (colonyData.attempts_by_coords as Record<string, ColonyAttemptCoordStats>)
        : attemptsSummary.by_coords && typeof attemptsSummary.by_coords === "object"
          ? attemptsSummary.by_coords
          : {},
    [colonyData, attemptsSummary]
  );
  const triedCount = Math.round(
    finiteNum(attemptsSummary.unique_coords_tried, Object.keys(attemptsByCoords).length)
  );
  const deletedCount = Math.round(
    finiteNum(
      attemptsSummary.deleted_undersized_count,
      attempts.filter((a) =>
        attemptOutcomeFor(a.event_type, minFieldsTarget).label.startsWith("DELETED")
      ).length
    )
  );
  const keptCount = Math.round(finiteNum(attemptsSummary.kept_count, 0));
  const totalEvents = Math.round(finiteNum(attemptsSummary.total_events, attempts.length));

  const handleSavePolicy = async () => {
    const minVal = Math.max(50, Math.min(600, Math.round(finiteNum(minFieldsInput, 300))));
    try {
      await apiCommand({
        command: "update_config",
        config: { min_colony_fields: minVal, allow_colonize: allowColonizeInput },
      });
      showFeedback(
        `Colony policy saved: min ${minVal} fields, auto-colonize=${allowColonizeInput}.`,
        "ok"
      );
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Failed updating colony policy: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleScanGalaxy = async () => {
    try {
      showFeedback("Scanning Galaxy system for free slots...", "ok");
      const res = await apiCommand({
        command: "scan_galaxy",
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(res.message || "Galaxy scan completed.", "ok");
      await loadDashboard({ refresh: false, force: true });
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Galaxy scan failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleProbeCandidate = async (coords: string) => {
    try {
      showFeedback(`Probing free slot ${coords} for field count...`, "ok");
      const res = await apiCommand({
        command: "spy",
        target_coords: coords,
        quantity: 1,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Spy probe dispatched to ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Probe failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleColonizeCandidate = async (coords: string) => {
    try {
      showFeedback(`Dispatching Colony ship (s8) to ${coords}...`, "ok");
      const res = await apiCommand({
        command: "colonize",
        target_coords: coords,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Colonization processed for ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await loadDashboard({ refresh: false, force: true });
      await refreshColoniesView();
    } catch (err: any) {
      showFeedback(`Colonize failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleDiscardCandidate = async (coords: string) => {
    try {
      const res = await apiCommand({
        command: "discard_colony_candidate",
        target_coords: coords,
      });
      showFeedback(res.message || `Discarded colony candidate ${coords}.`, "ok");
      await refreshColoniesView();
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Discard failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-colonies"
      className="page-view"
      aria-label="Possible colonies and planetary field grading"
      hidden={isHidden}
    >
      <ColoniesKpiBar
        colonyData={colonyData}
        gradeCounts={gc}
        minFieldsTarget={minFieldsTarget}
        triedCount={triedCount}
        deletedCount={deletedCount}
        keptCount={keptCount}
        totalEvents={totalEvents}
        minFieldsInput={minFieldsInput}
        allowColonizeInput={allowColonizeInput}
        onMinFieldsChange={setMinFieldsInput}
        onAllowColonizeChange={setAllowColonizeInput}
        onSavePolicy={handleSavePolicy}
        onScanGalaxy={handleScanGalaxy}
      />

      <ColoniesCandidatesTable
        colonyData={colonyData}
        galaxySweep={dashboard?.galaxy_sweep}
        allCands={allCands}
        colFilter={colFilter}
        onSelectFilter={(f) => setUiPref("colonyFilter", f)}
        minFieldsTarget={minFieldsTarget}
        attemptsByCoords={attemptsByCoords}
        onSyncInbox={syncAllInboxFolders}
        onProbeCandidate={handleProbeCandidate}
        onColonizeCandidate={handleColonizeCandidate}
        onDiscardCandidate={handleDiscardCandidate}
      />

      <ColoniesAttemptsTable
        attempts={attempts}
        triedCount={triedCount}
        minFieldsTarget={minFieldsTarget}
      />
    </section>
  );
};

export default ColoniesView;
