import React from "react";
import { feedbackKindForError, safeFiniteNumber } from "../../api/httpClient";
import { useConsole } from "../../context/ConsoleContext";
import { computeColonyGateInfo } from "../../features/action-graph/plannerHelpers";

function clampPct(val: unknown): number {
  const n = safeFiniteNumber(val, 0) ?? 0;
  return Math.max(0, Math.min(100, n));
}

function fmtIntLocal(n: unknown): string {
  if (n === null || n === undefined || n === "") return "0";
  const v = safeFiniteNumber(n, null);
  if (v === null) return "—";
  const clean = Math.abs(v) < 1e-3 && v < 0 && v > -0.05 ? 0 : v === 0 ? 0 : v;
  const formatted = clean.toLocaleString("en-US");
  return formatted === "-0" ? "0" : formatted;
}

function fmtCompactLocal(val: unknown): string {
  const n = safeFiniteNumber(val, null);
  if (n === null) return "—";
  const absN = Math.abs(n);
  if (absN >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (absN >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  if (Number.isInteger(n)) return fmtIntLocal(n);
  return absN < 0.05 ? "0.0" : n.toFixed(1);
}

export function fmtImprovementPct(rawPct: unknown, baseFit?: unknown, bestFit?: unknown): string {
  let pct = safeFiniteNumber(rawPct, null);
  const base = safeFiniteNumber(baseFit, null);
  const best = safeFiniteNumber(bestFit, null);
  if (pct === null && base !== null && best !== null) {
    if (Math.abs(base) > 1e-6) {
      pct = ((best - base) / Math.abs(base)) * 100;
    } else if (Math.abs(best) <= 1e-12) {
      pct = 0;
    } else {
      pct = best > 0 ? 999.9 : -999.9;
    }
  }
  if (pct === null) return "—";
  if (pct > 999.9) return ">+999.9%";
  if (pct < -999.9) return "<-999.9%";
  if (Math.abs(pct) < 0.05) pct = 0;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function geneShortTag(kind: unknown): string {
  const k = String(kind || "").trim().toUpperCase();
  if (!k) return "";
  if (k === "BUILD") return "BLD";
  if (k === "DEMOLISH_BUILDING" || k === "DEMOLISH") return "DEM";
  if (k === "WAIT_RESOURCES") return "WAIT";
  if (k === "RESEARCH") return "RES";
  if (k === "BUILD_SHIP" || k === "SHIPS") return "SHP";
  if (k === "ATTACK" || k === "RAID") return "ATK";
  if (k === "SPY") return "SPY";
  if (k === "TRANSPORT") return "TRN";
  if (k === "STATION") return "STN";
  if (k === "RECYCLE") return "REC";
  if (k === "COLONIZE") return "COL";
  if (k === "SCAN_GALAXY" || k === "GALAXY_SCAN") return "SCN";
  if (k === "THROTTLE_BUILDING") return "B2%";
  if (k === "CLAIM_TUTORIAL") return "TUT";
  return k.slice(0, 4);
}

export const GaNnScannerHud: React.FC = () => {
  const {
    dashboard,
    setDashboard,
    gaCalculating,
    beginGaCalc,
    endGaCalc,
    bumpDashboardSeq,
    lastOptimizerSummary,
    setLastOptimizerSummary,
    plannerJobsStatus,
    refreshPlannerJobsStatus,
    apiCommand,
    loadDashboard,
    showFeedback,
  } = useConsole();

  const cfgMode = String(
    dashboard?.effective_planner_mode ||
      dashboard?.config?.planner_mode ||
      dashboard?.planner_mode ||
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

  const histLen = Array.isArray(rawSum.generation_history) ? rawSum.generation_history.length : null;
  const genDone = Math.max(
    0,
    Math.round(safeFiniteNumber(rawSum.generation ?? rawSum.generations ?? histLen, 0) ?? 0)
  );
  const genTarget = Math.max(genDone, cfgGens);
  const jobPct = clampPct(runningJob?.progress_pct ?? 0);
  const aiStepsCompleted = Math.max(
    0,
    Math.round(safeFiniteNumber(aiSum?.steps_completed ?? aiSum?.steps_started, 0) ?? 0)
  );
  const rawAiStepsTotal = safeFiniteNumber(aiSum?.steps, null);
  const aiStepsTotal = Math.max(
    0,
    Math.round(rawAiStepsTotal ?? 0)
  );
  const genPct = !isActiveHud
    ? 0
    : runningJob
      ? jobPct
      : isAiMode && aiStepsTotal > 0
        ? clampPct((aiStepsCompleted / aiStepsTotal) * 100)
        : clampPct(genTarget > 0 ? (genDone / genTarget) * 100 : 0);

  const seedVal = safeFiniteNumber(rawSum.seed ?? dashboard?.config?.ga_seed, null);
  const seedTxt = seedVal !== null ? `Seed ${Math.round(seedVal)}` : "Auto seed";
  const genSubText = !isActiveHud
    ? "Waiting for optimizer pass"
    : runningJob
      ? String(runningJob.phase || "Running background optimizer...")
      : isAiMode
        ? `Colonies ${gate.coloniesOwned}/${gate.maxPlanets} · ${gate.liveAiUnlocked ? "Live Unlocked" : "Unlock at 11/11"}`
        : gaCalculating
          ? "Evolving action sequences..."
          : `Horizon ${horizonSteps} steps · ${seedTxt}`;

  const totalNetOutput = safeFiniteNumber(
    aiSum?.total_net_spice_eq ?? aiSum?.total_spice_eq,
    null
  );
  const hourlyLoot = safeFiniteNumber(
    aiSum?.attack_telemetry?.hourly_loot_spice_eq ?? aiSum?.hourly_loot_spice_eq,
    null
  );
  const nnEvals = Math.max(
    0,
    Math.round(safeFiniteNumber(rawSum.nn_evaluations ?? rawSum.neural_evals, 0) ?? 0)
  );
  const lossRaw = safeFiniteNumber(rawSum.nn_final_loss, null);
  const loss = lossRaw !== null ? Math.max(0, lossRaw) : null;
  const nnPct = !isActiveHud
    ? 0
    : isCalculating
      ? 72
      : isAiMode && !hasLegacySum
        ? totalNetOutput !== null && totalNetOutput > 0
          ? 78
          : 0
        : nnEvals > 0
          ? clampPct(Math.max(14, Math.log10(nnEvals + 1) * 38))
          : 0;
  const nnSubText = !isActiveHud
    ? "Output --"
    : isAiMode && !hasLegacySum
      ? hourlyLoot !== null
        ? `Attack Loot ${fmtCompactLocal(hourlyLoot)}/h`
        : "Horizon Net Production"
      : loss !== null
        ? `MSE Loss ${loss.toFixed(4)} · ${nnBackend}`
        : `Backend ${nnBackend} · Horizon ${horizonSteps}`;

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
    : impText.startsWith(">")
      ? 100
      : clampPct(
          rawImp !== null
            ? Math.max(0, Math.min(100, rawImp + 15))
            : bestFit !== null && bestFit > 0
              ? 35
              : 0
        );
  const fitSubText = isActiveHud
    ? `Base ${fmtCompactLocal(baseFit)} · Best ${fmtCompactLocal(bestFit)}`
    : "Base -- · Best --";

  const chrom = Array.isArray(rawSum.best_chromosome) ? rawSum.best_chromosome : [];
  const planLen = Array.isArray(dashboard?.plan) ? dashboard!.plan!.length : 0;
  const gLen = Math.max(
    0,
    Math.round(
      safeFiniteNumber(
        rawSum.genome_length ?? rawSum.actions_count ?? rawAiStepsTotal,
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
        const curDash = (globalThis as any).state?.dashboard || dashboard;
        if (curDash) {
          const updated = {
            ...curDash,
            plan: Array.isArray(res.actions) ? res.actions : curDash.plan,
            optimizer_summary: res.optimizer_summary ?? curDash.optimizer_summary,
          };
          if ((globalThis as any).state) {
            (globalThis as any).state.dashboard = updated;
          }
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
        <div className="gann-scanner-stage" aria-hidden="true">
          <svg
            id="gann-radial-svg"
            className="gann-radial-svg"
            viewBox="0 0 220 160"
            width="180"
            height="130"
            focusable="false"
          >
            <path
              className="gann-axis-rail"
              d="M 110 14 L 110 146"
              fill="none"
              stroke="#244249"
              strokeWidth="1"
              strokeDasharray="3 3"
            />
            <g transform="translate(110, 120) scale(1, 0.52)">
              <circle cx="0" cy="0" r="80" fill="none" stroke="#18343b" strokeWidth="1" />
              <g className="gann-ring-mid">
                <circle
                  cx="0"
                  cy="0"
                  r="66"
                  fill="none"
                  stroke="#16ec98"
                  strokeOpacity="0.48"
                  strokeWidth="2"
                  strokeDasharray="42 16 18 14"
                />
                <circle
                  cx="0"
                  cy="0"
                  r="46"
                  fill="none"
                  stroke="#32e8ff"
                  strokeOpacity="0.38"
                  strokeWidth="1.5"
                  strokeDasharray="20 12"
                />
              </g>
            </g>
            <g transform="translate(110, 80) scale(1, 0.52)">
              <circle cx="0" cy="0" r="84" fill="none" stroke="#243135" strokeWidth="1" />
              <g className="gann-ring-gold">
                <circle
                  cx="0"
                  cy="0"
                  r="52"
                  fill="none"
                  stroke="#efb03b"
                  strokeWidth="2.4"
                  strokeDasharray="22 8 10 8 34 10"
                />
                <circle cx="52" cy="0" r="3" fill="#efb03b" />
                <circle cx="-52" cy="0" r="3" fill="#efb03b" />
                <circle cx="0" cy="52" r="2.5" fill="#ffcf5c" />
                <circle cx="0" cy="-52" r="2.5" fill="#ffcf5c" />
              </g>
              <g className="gann-core-pulse">
                <circle cx="0" cy="0" r="26" fill="#091518" stroke="#16ec98" strokeWidth="1.8" />
                <circle
                  cx="0"
                  cy="0"
                  r="14"
                  fill="none"
                  stroke="#ffcf5c"
                  strokeOpacity="0.7"
                  strokeWidth="1.2"
                />
                <circle cx="0" cy="0" r="7" fill="#efb03b" />
              </g>
            </g>
            <g transform="translate(110, 42) scale(1, 0.52)">
              <circle
                cx="0"
                cy="0"
                r="84"
                fill="none"
                stroke="#2f6670"
                strokeWidth="4"
                strokeDasharray="1.5 6.0"
              />
              <g className="gann-ring-outer">
                <circle
                  cx="0"
                  cy="0"
                  r="72"
                  fill="none"
                  stroke="#32e8ff"
                  strokeWidth="2"
                  strokeDasharray="58 14 26 12 44 18"
                />
                <circle cx="0" cy="-72" r="3" fill="#32e8ff" />
                <circle cx="0" cy="72" r="3" fill="#16ec98" />
              </g>
              <g className="gann-scan-sweep">
                <path
                  d="M 0 -60 A 60 60 0 0 1 60 0"
                  fill="none"
                  stroke="#16ec98"
                  strokeWidth="3"
                  strokeLinecap="round"
                />
                <path
                  d="M 0 -46 L 0 -22 M 0 22 L 0 46 M -46 0 L -22 0 M 22 0 L 46 0"
                  fill="none"
                  stroke="#32e8ff"
                  strokeOpacity="0.72"
                  strokeWidth="1.2"
                />
              </g>
            </g>
          </svg>
        </div>

        <div className="gann-metrics-grid">
          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-cyan"
                id="gann-arc-gen"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(genPct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">
                  {anyJobRunning ? "Background Job" : isAiMode ? "Horizon Progress" : "Cycle Progress"}
                </span>
                <strong className="gann-metric-val tabular" id="gann-gen-val">
                  {!isActiveHud
                    ? "0 / --"
                    : runningJob
                      ? `${Math.round(jobPct)}%`
                      : isAiMode && aiStepsTotal > 0
                        ? `${aiStepsCompleted} / ${aiStepsTotal}`
                        : `${genDone} / ${genTarget}`}
                </strong>
              </div>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-cyan"
                id="gann-gen-bar"
                style={{ transform: `scaleX(${genPct / 100})` }}
              />
            </div>
            <span className="gann-metric-sub tabular" id="gann-gen-sub">
              {genSubText}
            </span>
          </div>

          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-mint"
                id="gann-arc-nn"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(nnPct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">
                  {isAiMode && !hasLegacySum ? "Horizon Output" : "Search Eval Pass"}
                </span>
                <strong className="gann-metric-val tabular" id="gann-nn-val">
                  {!isActiveHud
                    ? "0 evals"
                    : isAiMode && !hasLegacySum
                      ? totalNetOutput !== null
                        ? `${fmtCompactLocal(totalNetOutput)} SpEq`
                        : "— SpEq"
                      : `${fmtIntLocal(nnEvals)} evals`}
                </strong>
              </div>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-mint"
                id="gann-nn-bar"
                style={{ transform: `scaleX(${nnPct / 100})` }}
              />
            </div>
            <span className="gann-metric-sub tabular" id="gann-nn-sub">
              {nnSubText}
            </span>
          </div>

          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-gold"
                id="gann-arc-fit"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(fitPct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">Reward Gain</span>
                <strong className="gann-metric-val tabular" id="gann-fit-val">
                  {impText}
                </strong>
              </div>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-gold"
                id="gann-fit-bar"
                style={{ transform: `scaleX(${fitPct / 100})` }}
              />
            </div>
            <span className="gann-metric-sub tabular" id="gann-fit-sub">
              {fitSubText}
            </span>
          </div>

          <div className="gann-metric-cell">
            <div className="gann-metric-head">
              <div
                className="gann-mini-arc is-cyan"
                id="gann-arc-genome"
                aria-hidden="true"
                style={{ ["--arc-pct" as any]: String(genomePct) }}
              />
              <div className="gann-metric-titles">
                <span className="gann-metric-label">
                  {isAiMode ? "Horizon Sequence" : "Action Sequence"}
                </span>
                <strong className="gann-metric-val tabular" id="gann-genome-val">
                  {!isActiveHud
                    ? "0 steps"
                    : hasLegacySum && chrom.length > 0
                      ? `${gLen} genes`
                      : `${gLen} steps`}
                </strong>
              </div>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill gann-bar-cyan"
                id="gann-genome-bar"
                style={{ transform: `scaleX(${genomePct / 100})` }}
              />
            </div>
            <span className="gann-metric-sub tabular" id="gann-genome-sub">
              {genomeSubText}
            </span>
          </div>
        </div>
      </div>
    </article>
  );
};

export default GaNnScannerHud;
