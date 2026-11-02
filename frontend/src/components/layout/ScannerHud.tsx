import React, { useMemo } from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import {
  useConsoleActions,
  useDashboard,
  useGaCalc,
  useLastOptimizerSummary,
  usePlannerJobsStatus,
} from "../../store";
import { computeColonyGateInfo } from "../../features/action-graph/plannerHelpers";
import type { PlanetData, QueueJobData } from "../../types/api";
import {
  clampPct,
  computeHourlyProdData,
  computeLootRecycleData,
  countActiveBuildingPlanets,
  fmtCompactLocal,
  fmtImprovementPct,
  geneShortTag,
} from "./scannerHudTelemetry";
import { ScannerHudMetrics } from "./ScannerHudMetrics";
import { ScannerHudRadial } from "./ScannerHudRadial";

export { fmtImprovementPct };

export const ScannerHud: React.FC = () => {
  const dashboard = useDashboard();
  const lastOptimizerSummary = useLastOptimizerSummary();
  const plannerJobsStatus = usePlannerJobsStatus();
  const { gaCalculating } = useGaCalc();
  const {
    setDashboard,
    beginGaCalc,
    endGaCalc,
    bumpDashboardSeq,
    setLastOptimizerSummary,
    refreshPlannerJobsStatus,
    apiCommand,
    loadDashboard,
    showFeedback,
    getSnapshot,
  } = useConsoleActions();

  const cfgMode = String(
    dashboard?.config?.planner_mode ||
      dashboard?.planner_mode ||
      dashboard?.effective_planner_mode ||
      "priority"
  ).toLowerCase();
  const isAiMode = cfgMode === "ai_planner";
  const isLegacyGa = cfgMode === "ga_nn";

  const jobsPayload =
    plannerJobsStatus || dashboard?.planner_jobs_status || dashboard?.planner_status || null;
  const coJob = jobsPayload?.config_optimizer_job || null;
  const aiJob = jobsPayload?.ai_planner_job || null;
  const coRunning = String(coJob?.status || "").toLowerCase() === "running";
  const aiRunning = String(aiJob?.status || "").toLowerCase() === "running";
  const runningJob = aiRunning ? aiJob : coRunning ? coJob : null;
  const anyJobRunning = Boolean(runningJob);

  const isActiveHud = isAiMode || isLegacyGa || anyJobRunning;
  const isCalculating = Boolean(gaCalculating || anyJobRunning);

  const gate = computeColonyGateInfo(dashboard, jobsPayload);
  const aiSum =
    dashboard?.ai_planner_summary && typeof dashboard.ai_planner_summary === "object"
      ? dashboard.ai_planner_summary
      : null;

  const incomingSum =
    dashboard?.optimizer_summary ||
    dashboard?.ga_nn_telemetry ||
    dashboard?.optimizer_metrics ||
    null;
  const hasIncoming = Boolean(
    incomingSum && typeof incomingSum === "object" && Object.keys(incomingSum).length > 0
  );
  const rawSum: Record<string, any> = isActiveHud
    ? (hasIncoming ? incomingSum! : isLegacyGa ? lastOptimizerSummary : null) || {}
    : {};
  const hasLegacySum = Boolean(rawSum && Object.keys(rawSum).length > 0);
  const hasAiSummary = Boolean(aiSum && Object.keys(aiSum).length > 0);

  const cfgPop = Math.max(
    4,
    Math.round(safeFiniteNumber(dashboard?.config?.ga_population_size, 24) ?? 24)
  );
  const cfgGens = Math.max(
    1,
    Math.round(safeFiniteNumber(dashboard?.config?.ga_generations, 12) ?? 12)
  );
  const horizonSteps = Math.max(
    1,
    Math.round(safeFiniteNumber(dashboard?.config?.ga_horizon_steps, 6) ?? 6)
  );

  const statusPillText = !isActiveHud
    ? "STANDBY"
    : isCalculating
      ? "CALCULATING"
      : isAiMode && !gate.liveAiUnlocked
        ? "PREVIEW ONLY"
        : hasAiSummary || hasLegacySum
          ? "OPTIMIZED"
          : "ACTIVE";

  const popSize = Math.max(
    1,
    Math.round(safeFiniteNumber(rawSum.population_size ?? aiSum?.ga_population_size, cfgPop) ?? cfgPop)
  );
  const nnBackend = String(
    rawSum.nn_backend || (isAiMode ? `${gate.coloniesOwned}/${gate.maxPlanets} COL` : "MLP")
  ).toUpperCase();
  const elapsedMs = safeFiniteNumber(
    runningJob?.elapsed_ms ?? rawSum.elapsed_ms ?? aiSum?.elapsed_ms,
    null
  );
  const safeElapsed = elapsedMs !== null ? Math.max(0, Math.round(elapsedMs)) : null;
  const metaLineText = !isActiveHud
    ? "HEURISTIC STANDBY"
    : runningJob
      ? `${aiRunning ? "AI-PLANNER" : "CONFIG OPT"} ${Math.round(safeFiniteNumber(runningJob.progress_pct, 0) ?? 0)}%${safeElapsed !== null ? ` · ${safeElapsed}ms` : ""}`
      : isAiMode
        ? `COL ${gate.coloniesOwned}/${gate.maxPlanets} · W${Math.round(safeFiniteNumber(aiSum?.reward_weeks, 26) ?? 26)}${safeElapsed !== null ? ` · ${safeElapsed}ms` : ""}`
        : safeElapsed !== null
          ? `POP ${popSize} · ${nnBackend} · ${safeElapsed}ms`
          : `POP ${popSize} · ${nnBackend}`;

  const isLegacyGaSummaryMode = isLegacyGa && hasLegacySum;

  const histLen = Array.isArray(rawSum.generation_history) ? rawSum.generation_history.length : null;
  const genDone = Math.max(
    0,
    Math.round(safeFiniteNumber(rawSum.generation ?? rawSum.generations ?? histLen, 0) ?? 0)
  );
  const genTarget = Math.max(genDone, cfgGens);
  const jobPct = clampPct(runningJob?.progress_pct ?? 0);
  const rawAiStepsTotal = safeFiniteNumber(aiSum?.steps, null);
  const aiStepsTotal = Math.max(0, Math.round(rawAiStepsTotal ?? 0));

  const builderStats = useMemo(() => {
    const planets: PlanetData[] = Array.isArray(dashboard?.state?.planets)
      ? dashboard!.state!.planets!
      : [];
    const activeQueues: QueueJobData[] = Array.isArray(dashboard?.state?.active_queues)
      ? dashboard!.state!.active_queues!
      : [];
    const counted = countActiveBuildingPlanets(
      planets,
      activeQueues,
      dashboard?.state?.active_planet_id
    );
    const curProdObj = (dashboard as Record<string, any> | null | undefined)
      ?.current_hourly_production;
    const total =
      counted.total > 0
        ? counted.total
        : Math.max(
            0,
            Math.round(
              safeFiniteNumber(curProdObj?.planet_count ?? gate.coloniesOwned, 0) ?? 0
            )
          );
    const hasActiveQueuesField = Array.isArray(dashboard?.state?.active_queues);
    const activeRaw = hasActiveQueuesField
      ? counted.active
      : Math.max(0, Math.round(safeFiniteNumber(curProdObj?.active_builders, 0) ?? 0));
    const active = total > 0 ? Math.min(total, activeRaw) : activeRaw;
    return { active, total };
  }, [dashboard, gate.coloniesOwned]);

  const builderPct =
    builderStats.total > 0
      ? clampPct(Math.round((builderStats.active / builderStats.total) * 100))
      : 0;

  const genPct = !isActiveHud
    ? 0
    : runningJob
      ? jobPct
      : isLegacyGaSummaryMode
        ? clampPct(genTarget > 0 ? (genDone / genTarget) * 100 : 0)
        : builderPct;

  const seedVal = safeFiniteNumber(rawSum.seed ?? dashboard?.config?.ga_seed, null);
  const seedTxt = seedVal !== null ? `Seed ${Math.round(seedVal)}` : "Auto seed";
  const idleBuilders = Math.max(0, builderStats.total - builderStats.active);
  const genSubText = !isActiveHud
    ? "Waiting for builder telemetry"
    : runningJob
      ? String(runningJob.phase || "Running background optimizer...")
      : isLegacyGaSummaryMode
        ? gaCalculating
          ? "Evolving action sequences..."
          : `Horizon ${horizonSteps} steps · ${seedTxt}`
        : `${builderStats.active}/${builderStats.total} building · ${idleBuilders} idle · ${
            gate.liveAiUnlocked ? "Live Unlocked" : "Preview"
          }`;

  const hourlyProdData = useMemo(() => computeHourlyProdData(dashboard), [dashboard]);

  const nnEvals = Math.max(
    0,
    Math.round(safeFiniteNumber(rawSum.nn_evaluations ?? rawSum.neural_evals, 0) ?? 0)
  );
  const lossRaw = safeFiniteNumber(rawSum.nn_final_loss, null);
  const loss = lossRaw !== null ? Math.max(0, lossRaw) : null;
  const nnPct = !isActiveHud
    ? 0
    : isLegacyGaSummaryMode
      ? isCalculating
        ? 72
        : nnEvals > 0
          ? clampPct(Math.max(14, Math.log10(nnEvals + 1) * 38))
          : 0
      : hourlyProdData.arcPct;

  const nnSubText = !isActiveHud
    ? "Waiting for production samples"
    : isLegacyGaSummaryMode
      ? loss !== null
        ? `MSE Loss ${loss.toFixed(4)} · ${nnBackend}`
        : `Backend ${nnBackend} · Horizon ${horizonSteps}`
      : hourlyProdData.latest.total > 0
        ? `PI ${fmtCompactLocal(hourlyProdData.latest.pig)} · M ${fmtCompactLocal(hourlyProdData.latest.met)} · K ${fmtCompactLocal(hourlyProdData.latest.kry)} · S ${fmtCompactLocal(hourlyProdData.latest.spi)}${
            hourlyProdData.series.length >= 2 ? ` · ${hourlyProdData.series.length}m` : ""
          }`
        : "Waiting for hourly production samples";

  const lootRecycleData = useMemo(() => computeLootRecycleData(dashboard), [dashboard]);

  const baseFit = safeFiniteNumber(
    rawSum.baseline_fitness ?? rawSum.initial_best_fitness ?? aiSum?.baseline_discounted_reward,
    null
  );
  const bestFit = safeFiniteNumber(
    rawSum.best_fitness ??
      rawSum.fitness_score ??
      rawSum.final_best_fitness ??
      aiSum?.discounted_reward_spice_eq,
    null
  );
  const impText = isActiveHud
    ? fmtImprovementPct(rawSum.improvement_pct ?? aiSum?.improvement_pct, baseFit, bestFit)
    : "--%";
  const rawImp = safeFiniteNumber(rawSum.improvement_pct ?? aiSum?.improvement_pct, null);
  const fitPct = !isActiveHud
    ? 0
    : isLegacyGaSummaryMode
      ? impText.startsWith(">")
        ? 100
        : clampPct(
            rawImp !== null
              ? Math.max(0, Math.min(100, rawImp + 15))
              : bestFit !== null && bestFit > 0
                ? 35
                : 0
          )
      : lootRecycleData.arcPct;

  const fitSubText = !isActiveHud
    ? "Stolen 0 · Recycled 0 (24h)"
    : isLegacyGaSummaryMode
      ? `Base ${fmtCompactLocal(baseFit)} · Best ${fmtCompactLocal(bestFit)}`
      : `Stolen ${fmtCompactLocal(lootRecycleData.totalStolen)} · Recycled ${fmtCompactLocal(
          lootRecycleData.totalRecycled
        )} (${lootRecycleData.windowHours}h)`;

  const chrom = Array.isArray(rawSum.best_chromosome) ? rawSum.best_chromosome : [];
  const planLen = Array.isArray(dashboard?.plan) ? dashboard!.plan!.length : 0;
  const gLen = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        rawSum.genome_length ??
          rawSum.actions_count ??
          (isLegacyGaSummaryMode && chrom.length > 0 ? chrom.length : rawAiStepsTotal),
        chrom.length || planLen
      ) ?? 0
    )
  );
  const genomePct = !isActiveHud
    ? 0
    : clampPct(
        isAiMode && aiStepsTotal > 0
          ? (Math.min(gLen, aiStepsTotal) / aiStepsTotal) * 100
          : horizonSteps > 0
            ? (Math.min(gLen, horizonSteps) / horizonSteps) * 100
            : 0
      );
  const seqPreview = (chrom.length > 0 ? chrom : dashboard?.plan || [])
    .slice(0, 4)
    .map((g: any) => geneShortTag(typeof g === "string" ? g : g?.kind))
    .filter(Boolean)
    .join(" → ");
  const genomeSubText = !isActiveHud
    ? "Sequence standby"
    : seqPreview || (gLen > 0 ? `Top ${gLen} sequence steps` : "Sequence standby");

  const onRecalc = async () => {
    beginGaCalc();
    try {
      if (isAiMode) {
        showFeedback("Triggering AI-Planner recalculation...", "ok");
        await apiCommand({ command: "ai_planner_run", trigger_source: "hud_recalc" });
        await Promise.all([
          refreshPlannerJobsStatus(),
          loadDashboard({ refresh: false, preserveConfig: true, force: true }),
        ]);
        showFeedback("AI-Planner job triggered.", "ok");
      } else {
        showFeedback("Running planner recalculation...", "ok");
        const res = await apiCommand({
          command: "plan",
          planner_mode: isLegacyGa ? "ga_nn" : "priority",
          refresh: false,
        });
        bumpDashboardSeq();
        if (res.optimizer_summary) {
          setLastOptimizerSummary(res.optimizer_summary);
        }
        const curDash = getSnapshot().dashboard || dashboard;
        if (curDash) {
          const updated = {
            ...curDash,
            plan: Array.isArray(res.actions) ? res.actions : curDash.plan,
            optimizer_summary: res.optimizer_summary ?? curDash.optimizer_summary,
          };
          setDashboard(updated);
        } else {
          await loadDashboard({ refresh: false, preserveConfig: true, force: true });
        }
        showFeedback(
          `Evaluation complete (${(res.actions || []).length} planned actions).`,
          "ok"
        );
      }
    } catch (err: any) {
      showFeedback(`Recalculation failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      endGaCalc();
    }
  };

  return (
    <article
      className={`hud-window gann-hud${isActiveHud ? " is-active" : ""}${isCalculating ? " is-calculating" : ""}`}
      id="gann-calc-hud"
      aria-label="AI-Planner and background optimizer telemetry"
      hidden={!isActiveHud}
    >
      <div className="window-bar window-bar-wrap">
        <div className="window-title-group">
          <span className="module-code tabular">{isAiMode || anyJobRunning ? "AIP" : "GNN"}</span>
          <h2>
            {anyJobRunning
              ? "Optimizer Background Engine"
              : isAiMode
                ? "AI-Planner & Colony Telemetry"
                : "Planner Search Scanner"}
          </h2>
          <span className="gann-state-pill tabular" id="gann-status-pill" role="status">
            {statusPillText}
          </span>
        </div>
        <div className="inline-actions">
          <span className="module-meta tabular" id="gann-meta-line">
            {metaLineText}
          </span>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-gann-recalc"
            onClick={onRecalc}
          >
            Recalculate
          </button>
        </div>
      </div>

      <div className="gann-hud-body" id="gann-hud-body">
        <ScannerHudRadial />
        <ScannerHudMetrics
          isActiveHud={isActiveHud}
          isAiMode={isAiMode}
          isLegacyGaSummaryMode={isLegacyGaSummaryMode}
          anyJobRunning={anyJobRunning}
          hasRunningJob={Boolean(runningJob)}
          hasLegacyChrom={hasLegacySum && chrom.length > 0}
          genPct={genPct}
          jobPct={jobPct}
          genDone={genDone}
          genTarget={genTarget}
          builderStats={builderStats}
          genSubText={genSubText}
          nnPct={nnPct}
          nnEvals={nnEvals}
          hourlyProdData={hourlyProdData}
          nnSubText={nnSubText}
          fitPct={fitPct}
          impText={impText}
          lootRecycleData={lootRecycleData}
          fitSubText={fitSubText}
          genomePct={genomePct}
          gLen={gLen}
          genomeSubText={genomeSubText}
        />
      </div>
    </article>
  );
};

export default ScannerHud;
