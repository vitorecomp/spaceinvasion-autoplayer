import { safeFiniteNumber } from "../api/httpClient";
import { DashboardPayload, PlanetData } from "../types/api";

export function selectActivePlanet(
  dash: DashboardPayload | null | undefined,
  selectedPlanetId?: number | null
): PlanetData | null {
  const gs = dash?.state;
  if (!gs || !Array.isArray(gs.planets) || gs.planets.length === 0) return null;
  const targetId = selectedPlanetId || gs.active_planet_id;
  return gs.planets.find((p) => p.planet_id === targetId) || gs.planets[0] || null;
}

export function buildClientFallbackActionGraph(
  dash: DashboardPayload | null | undefined
): Record<string, any> {
  const cfg = dash?.config || {};
  const rawMode = String(
    dash?.effective_planner_mode || cfg.planner_mode || dash?.planner_mode || "priority"
  )
    .trim()
    .toLowerCase();
  const mode = rawMode === "ai_planner" ? "ai_planner" : "priority";
  const planList = Array.isArray(dash?.plan) ? dash!.plan! : [];
  const planets = Array.isArray(dash?.state?.planets) ? dash!.state!.planets! : [];
  const planetById = new Map<number, PlanetData>();
  planets.forEach((p) => {
    const pid = safeFiniteNumber(p?.planet_id, 0) ?? 0;
    if (pid > 0) planetById.set(pid, p);
  });

  const laneCounts = new Map<string, number>([["lane:global", 0]]);
  planets.forEach((p) => {
    const pid = safeFiniteNumber(p?.planet_id, 0) ?? 0;
    if (pid > 0) laneCounts.set(`lane:planet:${pid}`, 0);
  });

  let execRank = 0;
  const nodes = planList.map((act, idx) => {
    const kind = String(act?.kind || "BUILD").toUpperCase();
    const pid = Math.max(0, Math.round(safeFiniteNumber(act?.planet_id, 0) ?? 0));
    const pObj = planetById.get(pid) || null;
    const laneId =
      kind === "RESEARCH" || kind === "CLAIM_TUTORIAL" || pid <= 0
        ? "lane:global"
        : `lane:planet:${pid}`;
    laneCounts.set(laneId, (laneCounts.get(laneId) || 0) + 1);
    const execNow = Boolean(act?.executable_now);
    const missing = {
      pig_iron: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.pig_iron, 0) ?? 0)),
      metal: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.metal, 0) ?? 0)),
      kryptonite: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.kryptonite, 0) ?? 0)),
      spice: Math.max(0, Math.round(safeFiniteNumber(act?.missing?.spice, 0) ?? 0)),
    };
    const hasMissing =
      missing.pig_iron > 0 || missing.metal > 0 || missing.kryptonite > 0 || missing.spice > 0;
    const reason = String(act?.reason || "");
    const status = execNow
      ? "ready"
      : kind === "ALERT_HOSTILE" || reason.toLowerCase().includes("[hold:")
        ? "blocked_gate"
        : hasMissing || kind === "WAIT_RESOURCES"
          ? "waiting_resources"
          : "queued";
    return {
      id: `act_${idx}`,
      order_index: idx,
      exec_rank: execNow ? execRank++ : null,
      topological_layer: idx,
      lane_id: laneId,
      priority: String(act?.priority || "P2"),
      kind,
      category: kind === "RESEARCH" ? "research" : "economy",
      status,
      planet_id: pid,
      planet_coords: String(act?.planet_coords || pObj?.coords || ""),
      planet_name: String(pObj?.name || (pid > 0 ? `Colony ${pid}` : "Empire")),
      target_id: safeFiniteNumber(act?.target_id, null),
      target_name: String(act?.target_name || kind),
      target_level: safeFiniteNumber(act?.target_level, null),
      reason,
      executable_now: execNow,
      cost: {
        pig_iron: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.pig_iron, 0) ?? 0)),
        metal: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.metal, 0) ?? 0)),
        kryptonite: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.kryptonite, 0) ?? 0)),
        spice: Math.max(0, Math.round(safeFiniteNumber(act?.cost?.spice, 0) ?? 0)),
      },
      missing,
      wait_seconds: safeFiniteNumber(act?.wait_seconds, 0) ?? 0,
      wait_eta: String(act?.wait_eta || "Ready"),
      duration_seconds: Math.max(0, safeFiniteNumber(act?.duration_seconds, 0) ?? 0),
      duration_text: String(act?.duration_text || "Instant"),
      total_seconds: safeFiniteNumber(act?.total_seconds, 0) ?? 0,
      total_eta: String(act?.total_eta || "Ready"),
      eta_bottleneck: act?.eta_bottleneck ? String(act.eta_bottleneck) : null,
      ga_step: null,
      in_degree: idx > 0 ? 1 : 0,
      out_degree: idx + 1 < planList.length ? 1 : 0,
    };
  });

  const edges: Array<Record<string, any>> = [];
  for (let i = 0; i + 1 < nodes.length; i += 1) {
    const pType = nodes[i].lane_id === nodes[i + 1].lane_id ? "queue_order" : "execution_order";
    edges.push({
      id: `edge_act_${i}_act_${i + 1}`,
      source: `act_${i}`,
      target: `act_${i + 1}`,
      primary_type: pType,
      types: [pType],
      label: `Planned step #${i + 1} -> #${i + 2}`,
    });
  }

  const lanes: Array<Record<string, any>> = [];
  if (planets.length > 0 || nodes.length > 0) {
    lanes.push({
      id: "lane:global",
      scope: "global",
      label: "Empire & Global Research",
      planet_id: 0,
      coords: "",
      planet_type: "global",
      energy: 0,
      node_count: laneCounts.get("lane:global") || 0,
    });
    planets.forEach((p) => {
      const pid = Math.max(0, Math.round(safeFiniteNumber(p?.planet_id, 0) ?? 0));
      if (pid <= 0) return;
      const lid = `lane:planet:${pid}`;
      lanes.push({
        id: lid,
        scope: "planet",
        label: p?.coords
          ? `${p.name || `Colony ${pid}`} [${p.coords}]`
          : String(p?.name || `Colony ${pid}`),
        planet_id: pid,
        coords: String(p?.coords || ""),
        planet_type: String(p?.planet_type || "normal"),
        energy: Math.round(
          (safeFiniteNumber(p?.energy?.produced, 0) ?? 0) -
            (safeFiniteNumber(p?.energy?.used, 0) ?? 0)
        ),
        node_count: laneCounts.get(lid) || 0,
      });
    });
  }

  return {
    mode,
    simulated: false,
    state_available: Boolean(dash?.state),
    is_dirty: false,
    changed_keys: [],
    active_config: cfg,
    effective_config: cfg,
    lanes,
    nodes,
    edges,
    summary: {
      total_nodes: nodes.length,
      total_edges: edges.length,
      ready_count: nodes.filter((n) => n.status === "ready").length,
      waiting_count: nodes.filter((n) => n.status === "waiting_resources").length,
      blocked_count: nodes.filter((n) => n.status === "blocked_gate").length,
      queued_count: nodes.filter((n) => n.status === "queued").length,
      max_layer: Math.max(0, nodes.length - 1),
      critical_path_eta:
        nodes.length > 0 ? String(nodes[nodes.length - 1].total_eta || "Ready") : "Ready",
      primary_bottleneck: null,
      by_priority: { P0: 0, P1: 0, P2: nodes.length, P3: 0 },
      by_edge_type: {},
    },
    ga_summary: dash?.optimizer_summary || null,
  };
}
