import React from "react";
import type {
  BuildingResourceRequestItem,
  PlanetData,
  PlannedActionData,
  QueueJobData,
} from "../../types/api";
import { DashboardQueueCard } from "./DashboardQueueCard";

export interface DashboardQueuesSectionProps {
  hasDashboard: boolean;
  isAiPlanner: boolean;
  modeLabel: string;
  pinnedCount: number;
  planets: PlanetData[];
  constructingPlanetIds: ReadonlySet<number>;
  activeDashScope: string;
  scopedDashPlanet: PlanetData | null;
  planetQueueStats: ReadonlyMap<number, { total: number; ready: number }>;
  customPlanetNames?: Record<string, string>;
  dashQueueMaxRows: number | undefined;
  effNowSec: number;
  scopedExecBuildingJobs: QueueJobData[];
  buildingActions: PlannedActionData[];
  scopedBuildingResourceRequests: BuildingResourceRequestItem[];
  scopedExecFleetJobs: QueueJobData[];
  fleetActions: PlannedActionData[];
  execResearchJobs: QueueJobData[];
  researchActions: PlannedActionData[];
  onSelectAllScope: () => void;
  onSelectPlanetScope: (planetId: number) => void;
  onOpenComposerWithKind: (kind: string) => void;
  onGotoBuildings: () => void;
  onGotoCombat: () => void;
  onGotoResearch: () => void;
  onDispatch: (action: PlannedActionData, dryRun: boolean) => void;
  onUpdateQueue: (queueId: number, patch: Record<string, any>) => void;
  onCancelQueue: (queueId: number) => void;
  onPinQueue: (act: PlannedActionData, priority?: string) => void;
}

export const DashboardQueuesSection: React.FC<DashboardQueuesSectionProps> = ({
  hasDashboard,
  isAiPlanner,
  modeLabel,
  pinnedCount,
  planets,
  constructingPlanetIds,
  activeDashScope,
  scopedDashPlanet,
  planetQueueStats,
  customPlanetNames,
  dashQueueMaxRows,
  effNowSec,
  scopedExecBuildingJobs,
  buildingActions,
  scopedBuildingResourceRequests,
  scopedExecFleetJobs,
  fleetActions,
  execResearchJobs,
  researchActions,
  onSelectAllScope,
  onSelectPlanetScope,
  onOpenComposerWithKind,
  onGotoBuildings,
  onGotoCombat,
  onGotoResearch,
  onDispatch,
  onUpdateQueue,
  onCancelQueue,
  onPinQueue,
}) => (
  <>
    {/* Lower Row Toolbar */}
    <div className="dash-queues-toolbar" aria-label="Planned queues overview and planet scope">
      <div className="dash-queues-toolbar-meta">
        <span
          className={`module-meta tabular mode-badge${isAiPlanner ? " is-ga" : ""}`}
          id="dash-planner-mode-badge"
        >
          {modeLabel}
        </span>
        <span
          className="module-meta tabular"
          id="dash-pinned-count-badge"
          hidden={pinnedCount === 0}
        >
          {`${pinnedCount} PINNED`}
        </span>
        {planets.length > 1 ? (
          <span className="module-meta tabular" id="dash-builders-summary-badge">
            {`${constructingPlanetIds.size}/${planets.length} BUILDING`}
          </span>
        ) : null}
      </div>
      {planets.length > 1 ? (
        <div
          className="filter-chips dash-planet-scope-chips"
          role="group"
          aria-label="Filter building and fleet queues by planet"
        >
          <button
            type="button"
            className={`chip-btn planet-scope-chip tabular${activeDashScope === "all" ? " active" : ""}`}
            data-dash-planet-scope="all"
            title={`Show all ${planets.length} colonies (${constructingPlanetIds.size} currently constructing)`}
            onClick={onSelectAllScope}
          >
            <span className="planet-chip-top">All Planets</span>
            <span className="planet-chip-sub">
              {`${planets.length} colonies · ${constructingPlanetIds.size} bldg`}
            </span>
          </button>
          {planets.map((p) => {
            const pidKey = String(p.planet_id);
            const stats = planetQueueStats.get(p.planet_id) || { total: 0, ready: 0 };
            const isConstructing = constructingPlanetIds.has(Number(p.planet_id));
            const planetLabel = p.name || "Colony";
            return (
              <button
                key={pidKey}
                type="button"
                className={`chip-btn planet-scope-chip tabular${activeDashScope === pidKey ? " active" : ""}${isConstructing ? " is-constructing" : ""}`}
                data-dash-planet-scope={pidKey}
                data-constructing={isConstructing ? "true" : "false"}
                title={`${planetLabel} [${p.coords}] — ${isConstructing ? "Building in progress" : "Builder idle"} · ${stats.ready} ready / ${stats.total} queued`}
                onClick={() => onSelectPlanetScope(p.planet_id)}
              >
                <span className="planet-chip-top">{p.coords || planetLabel}</span>
                <span className="planet-chip-sub">
                  {isConstructing
                    ? `${planetLabel} · Building`
                    : `${planetLabel} (${stats.total})`}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>

    {/* Lower Row: 3 Side-by-Side Queues (Building, Fleet, Research) */}
    <div className="dash-lower-grid">
      <DashboardQueueCard
        cardId="dash-queue-card-building"
        moduleCode="NXQ"
        title="Building Queue"
        scopeBadge={
          planets.length > 1
            ? scopedDashPlanet
              ? scopedDashPlanet.name && scopedDashPlanet.name.toLowerCase() !== "colony"
                ? `${scopedDashPlanet.name} [${scopedDashPlanet.coords || ""}]`
                : scopedDashPlanet.coords || scopedDashPlanet.name
              : `ALL · ${planets.length} PLANETS`
            : undefined
        }
        countId="dash-bld-q-count"
        addBtnId="btn-dash-add-queue"
        addBtnAriaLabel="Queue building command"
        onAddQueueClick={() => onOpenComposerWithKind("BUILD")}
        gotoLabel="Buildings →"
        gotoPageAttr="buildings"
        onGotoClick={onGotoBuildings}
        execTbodyId="dash-exec-building-tbody"
        planTbodyId="dash-plan-tbody"
        execJobs={scopedExecBuildingJobs}
        plannedActions={buildingActions}
        resourceRequests={scopedBuildingResourceRequests}
        planets={planets}
        customPlanetNames={customPlanetNames}
        maxRows={dashQueueMaxRows}
        emptyText={
          hasDashboard
            ? scopedDashPlanet
              ? `No queued building actions on ${scopedDashPlanet.name || scopedDashPlanet.coords}.`
              : "No queued building actions."
            : "Run Sync to populate building actions..."
        }
        nowEpochSec={effNowSec}
        onDispatch={onDispatch}
        onUpdateQueue={onUpdateQueue}
        onCancelQueue={onCancelQueue}
        onPinQueue={onPinQueue}
      />

      <DashboardQueueCard
        cardId="dash-queue-card-fleet"
        moduleCode="FLT"
        title="Fleet Queue"
        scopeBadge={
          planets.length > 1
            ? scopedDashPlanet
              ? scopedDashPlanet.name && scopedDashPlanet.name.toLowerCase() !== "colony"
                ? `${scopedDashPlanet.name} [${scopedDashPlanet.coords || ""}]`
                : scopedDashPlanet.coords || scopedDashPlanet.name
              : "PER-PLANET"
            : undefined
        }
        countId="dash-fleet-q-count"
        addBtnId="btn-dash-add-fleet-queue"
        addBtnAriaLabel="Queue fleet or ship command"
        onAddQueueClick={() => onOpenComposerWithKind("BUILD_SHIP")}
        gotoLabel="Fleet →"
        gotoPageAttr="combat"
        onGotoClick={onGotoCombat}
        execTbodyId="dash-exec-fleet-tbody"
        planTbodyId="dash-plan-fleet-tbody"
        execJobs={scopedExecFleetJobs}
        plannedActions={fleetActions}
        planets={planets}
        customPlanetNames={customPlanetNames}
        maxRows={dashQueueMaxRows}
        emptyText={
          scopedDashPlanet
            ? `No queued fleet or ship actions on ${scopedDashPlanet.name || scopedDashPlanet.coords}.`
            : "No queued fleet or ship actions."
        }
        nowEpochSec={effNowSec}
        onDispatch={onDispatch}
        onUpdateQueue={onUpdateQueue}
        onCancelQueue={onCancelQueue}
        onPinQueue={onPinQueue}
      />

      <DashboardQueueCard
        cardId="dash-queue-card-research"
        moduleCode="RES"
        title="Research Queue"
        scopeBadge={planets.length > 1 ? "GLOBAL · 1 SLOT" : undefined}
        countId="dash-research-q-count"
        addBtnId="btn-dash-add-res-queue"
        addBtnAriaLabel="Queue research command"
        onAddQueueClick={() => onOpenComposerWithKind("RESEARCH")}
        gotoLabel="Research →"
        gotoBtnId="btn-dash-goto-research"
        gotoPageAttr="research"
        onGotoClick={onGotoResearch}
        execTbodyId="dash-exec-research-tbody"
        planTbodyId="dash-plan-research-tbody"
        execJobs={execResearchJobs}
        plannedActions={researchActions}
        planets={planets}
        customPlanetNames={customPlanetNames}
        maxRows={dashQueueMaxRows}
        emptyText="No queued research actions."
        nowEpochSec={effNowSec}
        onDispatch={onDispatch}
        onUpdateQueue={onUpdateQueue}
        onCancelQueue={onCancelQueue}
        onPinQueue={onPinQueue}
      />
    </div>
  </>
);
