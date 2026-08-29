const SESSION_STATE_KEY = "si_console_ui_state";

const state = {
  dashboard: null,
  selectedPlanetId: null,
  modeOverride: "auto",
  activePage: "dashboard",
  bldCategory: "all",
  selectedUnitKey: "b1",
  buildingsEtaData: null,
  combatData: null,
  combatSubTab: "ships",
  queueFilter: "all",
  activeLogSource: "commander",
  simPopulatedOnce: false,
  apiOnline: false,
  reconnectAttempts: 0,
  configDirty: false,
  configSaving: 0,
  gaCalcCount: 0,
  get gaCalcRunning() {
    return this.gaCalcCount > 0;
  },
  set gaCalcRunning(flag) {
    this.gaCalcCount = flag ? Math.max(1, this.gaCalcCount) : 0;
  },
  lastOptimizerSummary: null,
  uiVersions: {
    code_version: null,
    css_version: null,
    server_boot_id: null,
  },
  inFlightVersionCheck: false,
  inFlightDashboard: false,
  inFlightDashboardCount: 0,
  dashboardSeq: 0,
  feedbackTimer: null,
  actionGraphData: null,
  agSimDirty: false,
  agSimRunning: 0,
  agSimSeq: 0,
  agDebounceTimer: null,
  agEdgeFilter: "all",
  agParamTab: "core",
  agSelectedNodeId: null,
  espTab: "targets",
  espData: null,
  colonyFilter: "all",
  colonyData: null,
  atkTab: "battles",
  atkData: null,
  recTab: "harvests",
  emailCatFilter: "all",
  recData: null,
  notifications: { unread_count: 0, items: [] },
  notifFilter: "active",
  activeHelpNotifId: null,
  seenHelpNotifSigs: new Set(),
};

function savePersistedUiState() {
  try {
    sessionStorage.setItem(
      SESSION_STATE_KEY,
      JSON.stringify({
        activePage: state.activePage,
        selectedPlanetId: state.selectedPlanetId,
        bldCategory: state.bldCategory,
        selectedUnitKey: state.selectedUnitKey,
        combatSubTab: state.combatSubTab,
        queueFilter: state.queueFilter,
        activeLogSource: state.activeLogSource,
        agEdgeFilter: state.agEdgeFilter,
        agParamTab: state.agParamTab,
      })
    );
  } catch {
    // Ignore storage restriction
  }
}

function restorePersistedUiState() {
  try {
    const raw = sessionStorage.getItem(SESSION_STATE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return;
    const savedPid = finiteNum(saved.selectedPlanetId, null);
    if (savedPid !== null) state.selectedPlanetId = savedPid;
    if (saved.bldCategory) state.bldCategory = String(saved.bldCategory);
    if (saved.selectedUnitKey) state.selectedUnitKey = String(saved.selectedUnitKey);
    if (saved.combatSubTab) state.combatSubTab = String(saved.combatSubTab);
    if (saved.queueFilter) state.queueFilter = String(saved.queueFilter);
    if (saved.activeLogSource) state.activeLogSource = String(saved.activeLogSource);
    if (saved.agEdgeFilter === "all" || saved.agEdgeFilter === "deps") {
      state.agEdgeFilter = saved.agEdgeFilter;
    }
    if (saved.agParamTab === "core" || saved.agParamTab === "scope") {
      state.agParamTab = saved.agParamTab;
    }
    if (!window.location.hash && saved.activePage && PAGE_TITLES[saved.activePage]) {
      state.activePage = saved.activePage;
    }
  } catch {
    // Ignore malformed saved state
  }
}

const PAGE_TITLES = {
  dashboard: "DASHBOARD // OVERVIEW",
  buildings: "BUILDINGS // LEVEL ETAS",
  combat: "COMBAT & FLEET // 1:1 SIM",
  espionage: "ESPIONAGE // INTEL & ARCHIVE",
  colonies: "COLONIES // 300+ GRADING",
  attacks: "COMBAT // ATTACK RESULTS",
  recyclers: "RECYCLERS // RUBBLE & INBOX",
  operations: "OPERATIONS // PRIORITY QUEUE",
  "action-graph": "ACTION GRAPH // SEQUENCE & DAG",
  ranking: "RANKING // EVOLUTION & AZ",
  config: "STRATEGY // CONFIG BUCKETS",
  logs: "TERMINAL // LIVE LOGS",
};

const el = (id) => document.getElementById(id);

function fmtInt(n) {
  if (n === null || n === undefined || n === "") return "0";
  const v = finiteNum(n, null);
  if (v === null) return "—";
  const clean = v === 0 ? 0 : v;
  const formatted = clean.toLocaleString("en-US");
  return formatted === "-0" ? "0" : formatted;
}

function fmtRate(n) {
  if (n === null || n === undefined || n === "") return "0/h";
  const raw = finiteNum(n, null);
  if (raw === null) return "—/h";
  const rounded = Math.round(raw);
  const v = rounded === 0 ? 0 : rounded;
  const sign = v > 0 ? "+" : "";
  return `${sign}${fmtInt(v)}/h`;
}

function fmtClock(tsSec) {
  if (!tsSec) return "SYNC --:--:--";
  const ts = finiteNum(tsSec, null);
  if (ts === null) return "SYNC ACTIVE";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "SYNC ACTIVE";
  return `SYNC ${d.toTimeString().slice(0, 8)}`;
}

function showFeedback(message, kind = "ok", { autoHideMs = 0 } = {}) {
  const banner = el("feedback-banner");
  if (!banner) return;
  if (state.feedbackTimer) {
    clearTimeout(state.feedbackTimer);
    state.feedbackTimer = null;
  }
  banner.textContent = message;
  const cls = kind === "error" ? "is-error" : kind === "warn" ? "is-warn" : "is-ok";
  banner.className = `feedback-banner ${cls}`;
  banner.hidden = false;
  if (autoHideMs > 0) {
    state.feedbackTimer = setTimeout(() => {
      if (state.apiOnline) banner.hidden = true;
    }, autoHideMs);
  }
}

function setApiStatus(online, note = "") {
  const wasOnline = state.apiOnline;
  state.apiOnline = online;
  const pip = el("api-pip");
  const txt = el("api-status-text");
  const port = state.dashboard?.health?.debug_port ? "8787" : window.location.port || "8787";

  if (online) {
    const recovered = !wasOnline && state.reconnectAttempts > 0;
    state.reconnectAttempts = 0;
    if (pip) pip.className = "status-pip online";
    if (txt) txt.textContent = `API :${port} ONLINE · HOT`;
    if (recovered) {
      showFeedback(`Reconnected to Game API :${port}.`, "ok", { autoHideMs: 3200 });
    }
  } else {
    state.reconnectAttempts += 1;
    if (pip) pip.className = "status-pip offline";
    if (txt) txt.textContent = `API :${port} RECONNECTING (#${state.reconnectAttempts})`;
    showFeedback(
      `API offline (${note || "connection lost"}) — auto-reconnecting every 1.2s...`,
      "warn"
    );
  }
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 4000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal, cache: "no-store" });
  } finally {
    clearTimeout(timer);
  }
}

function ensureJsonObject(parsed, res, preview = "") {
  if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
    return parsed;
  }
  const kind = parsed === null ? "null" : Array.isArray(parsed) ? "array" : typeof parsed;
  const snippet = preview ? `: ${preview}` : "";
  const err = new Error(
    res && !res.ok
      ? `HTTP ${res.status}${snippet || ` (non-object JSON ${kind})`}`
      : `Invalid JSON object response (expected object, got ${kind})`
  );
  err.status = res?.status;
  err.rateLimited = res?.status === 429;
  throw err;
}

async function parseResponseJson(res) {
  if (typeof res?.text === "function") {
    const raw = await res.text();
    if (!raw || !raw.trim()) {
      return {};
    }
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      const preview = raw.replace(/\s+/g, " ").trim().slice(0, 120);
      const err = new Error(
        res.ok
          ? `Invalid JSON response (${preview || "empty"})`
          : `HTTP ${res.status}${preview ? `: ${preview}` : ""}`
      );
      err.status = res.status;
      err.rateLimited = res.status === 429;
      throw err;
    }
    const preview = raw.replace(/\s+/g, " ").trim().slice(0, 120);
    return ensureJsonObject(parsed, res, preview);
  }
  let parsed;
  try {
    parsed = await res.json();
  } catch (parseErr) {
    const err = new Error(
      res && !res.ok
        ? `HTTP ${res.status}`
        : `Invalid JSON response (${parseErr?.message || "parse error"})`
    );
    err.status = res?.status;
    err.rateLimited = res?.status === 429;
    throw err;
  }
  return ensureJsonObject(parsed, res);
}

async function apiGet(url, timeoutMs = 6000) {
  const res = await fetchWithTimeout(url, {}, timeoutMs);
  const data = ensureJsonObject(await parseResponseJson(res), res);
  if (!res.ok || data?.ok === false) {
    const err = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.rateLimited = Boolean(data?.rate_limited || res.status === 429);
    err.retryAfter = finiteNum(data?.retry_after, 0);
    throw err;
  }
  if (data?.notifications && typeof data.notifications === "object") {
    syncNotificationsPayload(data.notifications);
  }
  return data;
}

function isRateLimitError(err) {
  if (!err) return false;
  if (err.rateLimited || err.status === 429) return true;
  const msg = String(err.message || err || "").toLowerCase();
  return (
    msg.includes("rate-limit") ||
    msg.includes("rate limit") ||
    msg.includes("rate_limit") ||
    msg.includes("cooldown") ||
    msg.includes("30 page views") ||
    msg.includes("too many requests") ||
    msg.includes("retry after")
  );
}

function feedbackKindForError(err) {
  return isRateLimitError(err) ? "warn" : "error";
}

async function apiCommand(payload) {
  let res;
  try {
    res = await fetchWithTimeout(
      "/command",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      22000
    );
  } catch (err) {
    const msg = err?.name === "AbortError" ? "timeout" : err?.message || "unreachable";
    setApiStatus(false, msg);
    throw err;
  }
  const data = ensureJsonObject(await parseResponseJson(res), res);
  setApiStatus(true);
  if (!res.ok || data?.ok === false) {
    const err = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.rateLimited = Boolean(data?.rate_limited || res.status === 429);
    err.retryAfter = finiteNum(data?.retry_after, 0);
    throw err;
  }
  if (data?.notifications && typeof data.notifications === "object") {
    syncNotificationsPayload(data.notifications);
  }
  return data;
}

function activePlanetFrom(dash) {
  const gs = dash?.state;
  if (!gs || !Array.isArray(gs.planets) || gs.planets.length === 0) return null;
  const targetId = state.selectedPlanetId || gs.active_planet_id;
  return gs.planets.find((p) => p.planet_id === targetId) || gs.planets[0];
}

function currentBuildingsEta(dash) {
  if (dash && dash !== state.dashboard && dash.buildings_eta) {
    return dash.buildings_eta;
  }
  const planet = activePlanetFrom(dash);
  if (
    state.buildingsEtaData &&
    planet &&
    state.buildingsEtaData.planet?.planet_id === planet.planet_id
  ) {
    return state.buildingsEtaData;
  }
  return dash?.buildings_eta || null;
}

function currentCombat(dash) {
  if (dash && dash !== state.dashboard && dash.combat) {
    return dash.combat;
  }
  const planet = activePlanetFrom(dash);
  if (state.combatData && planet && state.combatData.planet_id === planet.planet_id) {
    return state.combatData;
  }
  return dash?.combat || null;
}

function syncModeButtons(modeVal) {
  state.modeOverride = modeVal;
  document.querySelectorAll("[data-mode]").forEach((btn) => {
    const active = btn.dataset.mode === modeVal;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-checked", active ? "true" : "false");
  });
}

function setActivePage(pageKey) {
  const target = PAGE_TITLES[pageKey] ? pageKey : "dashboard";
  state.activePage = target;
  savePersistedUiState();

  document.querySelectorAll(".page-view").forEach((view) => {
    view.hidden = view.id !== `view-${target}`;
  });
  document.querySelectorAll("[data-page]").forEach((btn) => {
    const isAct = btn.dataset.page === target;
    btn.classList.toggle("active", isAct);
    if (isAct) {
      btn.setAttribute("aria-current", "page");
    } else {
      btn.removeAttribute("aria-current");
    }
  });

  const heading = el("hud-page-title");
  if (heading) heading.textContent = PAGE_TITLES[target];

  if (window.location.hash !== `#${target}`) {
    window.history.replaceState(null, "", `#${target}`);
  }

  const sidebar = el("app-sidebar");
  const toggle = el("btn-sidebar-toggle");
  if (sidebar && sidebar.classList.contains("is-open")) {
    sidebar.classList.remove("is-open");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }

  if (target === "buildings") {
    refreshBuildingsEtaForSelectedPlanet();
  } else if (target === "combat") {
    refreshCombatForSelectedPlanet({ refresh: false });
  } else if (target === "espionage") {
    refreshEspionageView();
  } else if (target === "colonies") {
    refreshColoniesView();
  } else if (target === "attacks") {
    refreshAttacksView();
  } else if (target === "recyclers") {
    refreshRecyclersView();
  } else if (target === "action-graph") {
    if (state.actionGraphData) {
      renderActionGraphView(state.actionGraphData);
    } else if (state.dashboard?.action_graph) {
      state.actionGraphData = state.dashboard.action_graph;
      renderActionGraphView(state.actionGraphData);
    } else if (state.dashboard) {
      state.actionGraphData = buildClientFallbackGraph(state.dashboard);
      renderActionGraphView(state.actionGraphData);
    } else if (state.apiOnline) {
      refreshActiveActionGraph(false);
    }
  }
}

function applyVersionSnapshot(vData) {
  if (!vData) return false;
  const prev = state.uiVersions;
  if (prev.code_version == null) {
    state.uiVersions = {
      code_version: vData.code_version || null,
      css_version: vData.css_version || null,
      server_boot_id: vData.server_boot_id || null,
    };
    return false;
  }

  const codeChanged = Boolean(vData.code_version && vData.code_version !== prev.code_version);
  const bootChanged = Boolean(
    vData.server_boot_id && prev.server_boot_id && vData.server_boot_id !== prev.server_boot_id
  );
  if (codeChanged || bootChanged) {
    savePersistedUiState();
    showFeedback(
      codeChanged
        ? "UI update detected — hot-reloading console..."
        : "Game API restarted — hot-reloading console...",
      "ok"
    );
    setTimeout(() => window.location.reload(), 120);
    return true;
  }

  if (vData.css_version && vData.css_version !== prev.css_version) {
    state.uiVersions.css_version = vData.css_version;
    const link = el("ui-stylesheet");
    if (link) {
      link.href = `/styles.css?v=${encodeURIComponent(vData.css_version)}`;
    }
    showFeedback("Hot-reloaded styles.css live.", "ok", { autoHideMs: 2600 });
  }
  return false;
}

async function checkApiHealthAndHotReload() {
  if (state.inFlightVersionCheck) return;
  state.inFlightVersionCheck = true;
  try {
    const vData = await apiGet("/ui-version", 2500);
    const reloading = applyVersionSnapshot(vData);
    if (reloading) return;
    const wasOffline = !state.apiOnline;
    setApiStatus(true);
    if (wasOffline) {
      await loadDashboard({ refresh: false, preserveConfig: true });
    }
  } catch (err) {
    const msg = err?.name === "AbortError" ? "timeout" : err?.message || "unreachable";
    setApiStatus(false, msg);
  } finally {
    state.inFlightVersionCheck = false;
  }
}

async function refreshBuildingsEtaForSelectedPlanet(forceRefresh = false) {
  const planet = activePlanetFrom(state.dashboard);
  if (!planet) return;
  try {
    const res = await apiCommand({
      command: "buildings_eta",
      planet_id: planet.planet_id,
      refresh: forceRefresh,
    });
    if (res.buildings_eta) {
      state.buildingsEtaData = res.buildings_eta;
      if (state.dashboard) {
        renderTelemetry(state.dashboard);
        renderBuildingsView(state.dashboard);
        renderDashNearestEtas(state.dashboard);
      }
    }
  } catch {
    // Non-fatal during offline fallback
  }
}

async function refreshCombatForSelectedPlanet({ refresh = false } = {}) {
  const planet = activePlanetFrom(state.dashboard);
  if (!planet) return false;
  try {
    const res = await apiCommand({
      command: "combat_overview",
      planet_id: planet.planet_id,
      refresh,
    });
    if (res.combat) {
      state.combatData = res.combat;
      if (state.dashboard) {
        renderCombatView(state.dashboard);
      }
    }
    return true;
  } catch (err) {
    if (refresh || isRateLimitError(err)) {
      showFeedback(`Shipyard refresh failed: ${err.message}`, feedbackKindForError(err));
    }
    return false;
  }
}

function renderTopbar(dash) {
  const health = dash.health || {};
  const pip = el("chrome-pip");
  const statusText = el("chrome-status-text");
  if (health.in_game) {
    pip.className = "status-pip online";
    const cdSec = Math.ceil(finiteNum(health.retry_after, 0));
    const cdTag = health.rate_limited && cdSec > 0 ? ` · COOLDOWN ${cdSec}s` : health.rate_limited ? " · COOLDOWN" : "";
    statusText.textContent = `CHROME :${health.debug_port || 9222} ONLINE${cdTag}`;
    el("btn-ensure-session").hidden = true;
  } else if (health.debug_port_open) {
    pip.className = "status-pip";
    statusText.textContent = `CHROME :${health.debug_port || 9222} STANDBY`;
    el("btn-ensure-session").hidden = false;
  } else {
    pip.className = "status-pip offline";
    statusText.textContent = `CHROME :${health.debug_port || 9222} OFFLINE`;
    el("btn-ensure-session").hidden = false;
  }

  const gs = dash.state || {};
  el("hud-universe").textContent = `U${gs.universe_id || 31} · ${gs.nickname || "COMMANDER"}`;
  el("hud-clock").textContent = fmtClock(gs.timestamp);
  el("matrix-account-line").textContent = `CMD // ${gs.nickname || "OPERATOR"} · :${health.debug_port || 9222}`;

  const botCtrl = dash.bot_control || {};
  const paused = Boolean(botCtrl.paused);
  const pauseBtn = el("btn-pause-toggle");
  pauseBtn.setAttribute("aria-pressed", paused ? "true" : "false");
  pauseBtn.classList.toggle("btn-warning", paused);
  el("pause-label").textContent = paused ? "Resume Auto" : "Pause Auto";

  const modeVal = botCtrl.mode_override || "auto";
  syncModeButtons(modeVal);

  const runBadge = el("loop-run-badge");
  if (paused) {
    runBadge.textContent = "PAUSED";
    runBadge.className = "run-badge tabular is-paused";
  } else if (modeVal === "dry-run") {
    runBadge.textContent = "DRY";
    runBadge.className = "run-badge tabular is-dry";
  } else {
    runBadge.textContent = "RUN";
    runBadge.className = "run-badge tabular";
  }

  const planetSel = el("planet-select");
  const planets = gs.planets || [];
  if (planets.length > 0) {
    const chosen = activePlanetFrom(dash)?.planet_id;
    planetSel.replaceChildren();
    for (const p of planets) {
      const opt = document.createElement("option");
      opt.value = String(p.planet_id);
      opt.textContent = `${p.coords} ${p.name}`;
      if (p.planet_id === chosen) opt.selected = true;
      planetSel.appendChild(opt);
    }
  }
}

function renderResourceBar(key, amount, storage, hourlyRate) {
  const cap = finiteNum(storage, 0);
  const val = finiteNum(amount, null);
  const ratioPct = cap > 0 && val !== null ? (val / cap) * 100 : 0;
  const pct = Number.isFinite(ratioPct) ? Math.max(0, Math.min(100, Math.round(ratioPct))) : 0;
  el(`val-${key}`).textContent = fmtInt(amount ?? 0);
  el(`cap-${key}`).textContent = cap > 0 ? `/ ${fmtInt(cap)} (${pct}%)` : "—";
  const bar = el(`bar-${key}`);
  bar.style.transform = `scaleX(${pct / 100})`;
  bar.classList.toggle("high", pct >= 85);

  const rateNode = el(`rate-${key}`);
  if (rateNode) {
    if (hourlyRate == null) {
      rateNode.textContent = "—/h";
      rateNode.className = "track-rate";
    } else {
      const rateNum = finiteNum(hourlyRate, null);
      rateNode.textContent = fmtRate(hourlyRate);
      rateNode.className = `track-rate${rateNum !== null && Math.round(rateNum) < 0 ? " is-neg" : ""}`;
    }
  }
}

function getSelectedBlastFurnacePct(dash) {
  const planet = activePlanetFrom(dash);
  const eta = currentBuildingsEta(dash);
  const r2 = finiteNum(planet?.economy_rates?.resource2, null);
  const etaPct = finiteNum(eta?.planet_economy?.blast_furnace_pct, null);
  const pct = r2 !== null ? r2 : etaPct !== null ? etaPct : 100;
  return clampPct(Math.round(pct));
}

function renderTelemetry(dash) {
  const planet = activePlanetFrom(dash);
  const eta = currentBuildingsEta(dash);
  const pEcon = eta?.planet_economy || null;

  if (planet) {
    const planetTitle = String(planet.name || "COLONY").toUpperCase();
    el("command-subpath").textContent = `SECTOR // [${planet.coords || "--"}] ${planetTitle}`;
    renderResourceBar("pig", planet.resources?.pig_iron, planet.storage?.pig_iron, pEcon?.pig_iron_per_hr);
    renderResourceBar("metal", planet.resources?.metal, planet.storage?.metal, pEcon?.metal_per_hr);
    renderResourceBar("kryp", planet.resources?.kryptonite, planet.storage?.kryptonite, pEcon?.kryptonite_per_hr);
    renderResourceBar("spice", planet.resources?.spice, planet.storage?.spice, pEcon?.spice_per_hr);

    const prodRaw = planet.energy?.produced;
    const usedRaw = planet.energy?.used;
    const prodFinite = prodRaw == null ? 0 : finiteNum(prodRaw, null);
    const usedFinite = usedRaw == null ? 0 : finiteNum(usedRaw, null);
    const netRaw = prodFinite !== null && usedFinite !== null ? prodFinite - usedFinite : null;
    const net = netRaw === null ? null : netRaw === 0 ? 0 : netRaw;
    el("val-energy").textContent = net === null ? "—" : `${net >= 0 ? "+" : ""}${fmtInt(net)}`;
    el("energy-ratio").textContent = `${prodFinite === null ? "—" : fmtInt(prodFinite)} / ${usedFinite === null ? "—" : fmtInt(usedFinite)} E`;
    const tempVal = finiteNum(planet.temperature, null);
    const fUsedVal = finiteNum(planet.fields_used, null);
    const fMaxVal = finiteNum(planet.fields_max, null);
    el("planet-specs").textContent = `TEMP ${tempVal === null ? "—" : tempVal}°C · FIELDS ${fUsedVal === null ? "—" : fUsedVal}/${fMaxVal === null ? "—" : fMaxVal}`;
  }

  const b2Pct = getSelectedBlastFurnacePct(dash);
  const bfPill = el("hud-bf-pill");
  if (bfPill) {
    bfPill.textContent = b2Pct === 0 ? "B2: 0% PAUSED" : `B2: ${b2Pct}%`;
    bfPill.classList.toggle("is-paused", b2Pct === 0);
  }
  const dashB2Btn = el("btn-dash-b2-toggle");
  if (dashB2Btn) {
    dashB2Btn.textContent = b2Pct === 0 ? "Resume b2 (100%)" : "Pause b2 (0% +Pig)";
    dashB2Btn.className = b2Pct === 0 ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm";
    dashB2Btn.dataset.targetPct = b2Pct === 0 ? "100" : "0";
  }

  const rnk = dash.ranking || {};
  const cur = rnk.current || {};
  const rates = rnk.rates || {};
  const az = cur.attack_zone || {};
  const rankFinite = finiteNum(cur.server_rank, null);
  el("dash-rank-val").textContent = cur.server_rank != null ? (rankFinite !== null ? `#${fmtInt(rankFinite)}` : "#—") : az.label || "#—";
  const baselinePts = finiteNum(cur.baseline_points, 0);
  const steadyRate = finiteNum(rates.pts_per_hr_steady, 0);
  const spiceEqRate = finiteNum(rates.spice_eq_per_hr, 0);
  el("dash-az-val").textContent = `${az.label || "AZ —"} · ${fmtInt(Math.round(baselinePts))} pts`;
  const cleanSteady = Math.abs(steadyRate) < 0.005 || Number(steadyRate.toFixed(2)) === 0 ? 0 : steadyRate;
  el("dash-pts-hr").textContent = `${cleanSteady >= 0 ? "+" : ""}${cleanSteady.toFixed(2)}/h`;
  el("dash-spice-hr").textContent = `${fmtInt(Math.round(spiceEqRate))} SpiceEq/h`;

  const health = dash.health || {};
  const batchIv = finiteNum(health.action_batch_interval_seconds ?? dash.config?.action_batch_interval_seconds, null);
  el("val-budget").textContent = batchIv !== null && batchIv > 0 ? `BATCH ${Math.round(batchIv)}s` : `${health.remaining_hourly_actions ?? "—"} OPS/HR`;
  const spdVal = finiteNum(dash.state?.speedup_factor, null);
  el("val-speedup").textContent = spdVal !== null && spdVal > 0 ? `SPEED x${spdVal}` : "STD x1";

  const hostileCount = (dash.state?.fleet_alerts || []).filter((a) => a.hostile).length;
  const beacon = el("hud-alert-beacon");
  beacon.classList.toggle("is-alert", hostileCount > 0);
  el("val-alerts").textContent = hostileCount > 0 ? `HOSTILE ALERT (${hostileCount})` : "SECTOR CLEAR · 0 HOSTILES";

  const tut = dash.state?.tutorial;
  const tutBar = el("tutorial-strip");
  if (tut) {
    tutBar.hidden = false;
    el("tutorial-title").textContent = `TUTORIAL ${tut.step}/${tut.total_steps}`;
    const incomplete = (tut.tasks || []).filter((t) => t.current < t.target);
    el("tutorial-tasks-summary").textContent =
      incomplete.length > 0
        ? incomplete.map((t) => `${t.building_name || t.description} (${t.current}/${t.target})`).join(" · ")
        : `Ready to claim: ${tut.reward_text || "Step reward"}`;
    el("btn-claim-tutorial").hidden = !tut.can_complete;
  } else {
    tutBar.hidden = true;
  }
}

function renderColonyMatrix(dash) {
  const gs = dash.state || {};
  const planets = gs.planets || [];
  const chosenId = activePlanetFrom(dash)?.planet_id;
  el("colony-count-tag").textContent = `${String(planets.length).padStart(2, "0")}-${String(gs.universe_id || 31)}`;

  const tbody = el("colony-tbody");
  if (planets.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "empty-cell";
    td.textContent = "No colonies discovered.";
    tr.appendChild(td);
    tbody.replaceChildren(tr);
  } else {
    const rows = [];
    for (const p of planets) {
      const tr = document.createElement("tr");
      tr.className = "clickable-row";
      if (p.planet_id === chosenId) tr.classList.add("is-active");
      tr.addEventListener("click", () => {
        state.selectedPlanetId = p.planet_id;
        el("planet-select").value = String(p.planet_id);
        onPlanetChanged();
      });

      const tdCoords = document.createElement("td");
      tdCoords.className = "tabular";
      tdCoords.textContent = p.coords || "--";

      const tdName = document.createElement("td");
      tdName.textContent = p.name || "Colony";

      const tdStat = document.createElement("td");
      const badge = document.createElement("span");
      const pigStor = finiteNum(p.storage?.pig_iron, 0);
      const pigAmt = finiteNum(p.resources?.pig_iron, 0);
      const spiStor = finiteNum(p.storage?.spice, 0);
      const spiAmt = finiteNum(p.resources?.spice, 0);
      const nearFull =
        (pigStor > 0 && pigAmt / pigStor >= 0.85) ||
        (spiStor > 0 && spiAmt / spiStor >= 0.85);
      badge.className = `status-word${nearFull ? " is-warn" : ""}`;
      badge.textContent = nearFull ? "NEAR-CAP" : p.planet_id === chosenId ? "ONLINE" : "READY";
      tdStat.appendChild(badge);

      const tdFields = document.createElement("td");
      tdFields.className = "col-right tabular";
      const fUsed = finiteNum(p.fields_used, null);
      const fMax = finiteNum(p.fields_max, null);
      tdFields.textContent = `${fUsed === null ? "—" : fUsed}/${fMax === null ? "—" : fMax}`;

      tr.append(tdCoords, tdName, tdStat, tdFields);
      rows.push(tr);
    }
    tbody.replaceChildren(...rows);
  }

  const queues = gs.active_queues || [];
  const qNode = el("active-queues-list");
  if (queues.length === 0) {
    qNode.textContent = "No active construction or research jobs.";
  } else {
    qNode.replaceChildren();
    queues.forEach((q, idx) => {
      if (idx > 0) {
        qNode.appendChild(document.createTextNode(" · "));
      }
      const span = document.createElement("span");
      const catTag = `[${String(q.category || "JOB").toUpperCase()}]`;
      const titleTxt = cleanQueueJobTitle(q);
      const finTs = finiteNum(q.finish_ts, null);
      if (finTs !== null && finTs > 0) {
        const remNow = Math.max(0, Math.round(finTs - Date.now() / 1000));
        const remSpan = document.createElement("span");
        remSpan.dataset.finishTs = String(finTs);
        remSpan.textContent = formatCountdownHms(remNow);
        const finClock = formatFinishClock(finTs);
        span.append(
          document.createTextNode(`${catTag} ${titleTxt} (`),
          remSpan,
          document.createTextNode(` · ${finClock})`)
        );
      } else if (q.remaining_text) {
        span.textContent = `${catTag} ${titleTxt} (${q.remaining_text})`;
      } else {
        span.textContent = `${catTag} ${titleTxt}`;
      }
      qNode.appendChild(span);
    });
  }
}

function formatResourcesInline(res) {
  if (!res) return "—";
  const parts = [];
  if (res.pig_iron) parts.push(`${fmtInt(res.pig_iron)} Pig`);
  if (res.metal) parts.push(`${fmtInt(res.metal)} Met`);
  if (res.kryptonite) parts.push(`${fmtInt(res.kryptonite)} Kryp`);
  if (res.spice) parts.push(`${fmtInt(res.spice)} Spi`);
  return parts.join(" · ") || "0";
}

const MQ_BUILDINGS_CATALOG = [
  { id: 1, name: "Pig iron mine" },
  { id: 2, name: "Blast furnace" },
  { id: 3, name: "Kryptonite mine" },
  { id: 4, name: "Spice mine" },
  { id: 5, name: "Fusion plant" },
  { id: 6, name: "Thermosolar plant" },
  { id: 7, name: "Development center" },
  { id: 8, name: "Research lab" },
  { id: 9, name: "Weapons factory" },
  { id: 10, name: "Fleet base" },
  { id: 11, name: "Pig iron warehouse" },
  { id: 12, name: "Metal warehouse" },
  { id: 13, name: "Kryptonite warehouse" },
  { id: 14, name: "Spice warehouse" },
  { id: 15, name: "Particle cannon" },
  { id: 16, name: "Particle shield" },
  { id: 17, name: "Microsystem accelerator" },
  { id: 18, name: "Teleporter" },
  { id: 19, name: "Galaxy scanner" },
  { id: 20, name: "Tunnel system" },
  { id: 21, name: "Quantum Array" },
];

const MQ_RESEARCH_CATALOG = [
  { id: 1, name: "Mining technology" },
  { id: 2, name: "Energy technology" },
  { id: 3, name: "Engine research" },
  { id: 4, name: "Rocket engine" },
  { id: 5, name: "Diffusion engine" },
  { id: 6, name: "Warp engine" },
  { id: 7, name: "Spy technology" },
  { id: 8, name: "Weapons technology" },
  { id: 9, name: "Shield technology" },
  { id: 10, name: "Defense systems" },
  { id: 11, name: "Targeting systems" },
  { id: 12, name: "Particle analyzer" },
  { id: 13, name: "Graviton technology" },
  { id: 14, name: "Teleportation" },
  { id: 15, name: "Black hole research" },
];

const MQ_SHIPS_CATALOG = [
  { id: 1, name: "Small transport", key: "s1" },
  { id: 2, name: "Large transport", key: "s2" },
  { id: 3, name: "Transmitter", key: "s3" },
  { id: 4, name: "Small fighter", key: "s4" },
  { id: 5, name: "Warship", key: "s5" },
  { id: 6, name: "Starcruiser", key: "s6" },
  { id: 7, name: "Phoenix", key: "s7" },
  { id: 8, name: "Colony ship", key: "s8" },
  { id: 9, name: "Solar cells", key: "s9" },
  { id: 10, name: "Spy probe", key: "s10" },
  { id: 11, name: "Recycler", key: "s11" },
  { id: 13, name: "Battleship", key: "s13" },
  { id: 14, name: "Stealth bomber", key: "s14" },
  { id: 15, name: "Destroyer", key: "s15" },
  { id: 16, name: "Imperial starbase", key: "s16" },
];

async function enqueueManualCommand(params) {
  try {
    const res = await apiCommand({ command: "queue_add", ...params });
    showFeedback(res.message || "Command added to priority queue.", "ok");
    await loadDashboard({ refresh: false, force: true });
    return res;
  } catch (err) {
    showFeedback(`Queue add failed: ${err.message}`, feedbackKindForError(err));
    return null;
  }
}

async function updateManualQueueItem(queueId, patch) {
  try {
    const res = await apiCommand({ command: "queue_update", queue_id: queueId, ...patch });
    showFeedback(res.message || `Updated queue item #${queueId}.`, "ok");
    await loadDashboard({ refresh: false, force: true });
    return res;
  } catch (err) {
    showFeedback(`Queue update failed: ${err.message}`, feedbackKindForError(err));
    return null;
  }
}

async function cancelManualQueueItem(queueId) {
  try {
    const res = await apiCommand({ command: "queue_cancel", queue_id: queueId });
    showFeedback(res.message || `Removed queue item #${queueId}.`, "ok");
    await loadDashboard({ refresh: false, force: true });
    return res;
  } catch (err) {
    showFeedback(`Cancel failed: ${err.message}`, feedbackKindForError(err));
    return null;
  }
}

async function clearAllManualQueue() {
  try {
    const res = await apiCommand({ command: "queue_clear" });
    showFeedback(res.message || "Cleared manual queue.", "ok");
    await loadDashboard({ refresh: false, force: true });
  } catch (err) {
    showFeedback(`Clear queue failed: ${err.message}`, feedbackKindForError(err));
  }
}

function pinPlannedActionToQueue(act, priority = "P1") {
  const normKind = act.kind === "WAIT_RESOURCES" ? "BUILD" : String(act.kind || "").toUpperCase();
  const mCoords = /(\d{1,2}:\d{1,3}(?::\d{1,2})?)/.exec(act.target_name || "");
  return enqueueManualCommand({
    kind: normKind,
    priority,
    position: "top",
    planet_id: act.planet_id || 0,
    target_id: act.target_id ?? undefined,
    target_name: act.target_name || "",
    target_coords: mCoords ? mCoords[1] : undefined,
    target_level: act.target_level ?? undefined,
    deduplicate_pending: true,
  });
}

function syncManualQueueComposer(dash, { forceDefaultLevel = false } = {}) {
  const form = el("mq-composer-form");
  if (!form) return;
  const activeEl = document.activeElement;
  if (!forceDefaultLevel && activeEl && form.contains(activeEl) && activeEl.tagName === "INPUT") {
    return;
  }

  const pSel = el("mq-planet");
  const kSel = el("mq-kind");
  const uWrap = el("mq-unit-wrap");
  const uLbl = el("mq-unit-label");
  const uSel = el("mq-target-id");
  const cWrap = el("mq-coords-wrap");
  const cLbl = el("mq-coords-label");
  const cInp = el("mq-coords");
  const cList = el("mq-coords-datalist");
  const lWrap = el("mq-level-wrap");
  const lLbl = el("mq-level-label");
  const lInp = el("mq-level");
  if (!pSel || !kSel || !uSel || !cInp || !lInp) return;

  const planets = dash?.state?.planets || [];
  const activeP = activePlanetFrom(dash) || planets[0] || null;
  const prevPid = Number(pSel.value || 0);
  const targetPid = planets.some((p) => p.planet_id === prevPid)
    ? prevPid
    : activeP
      ? activeP.planet_id
      : 0;

  if (planets.length > 0) {
    const sig = planets.map((p) => `${p.planet_id}:${p.coords}:${p.name}`).join("|");
    if (pSel.dataset.sig !== sig) {
      pSel.dataset.sig = sig;
      pSel.replaceChildren(
        ...planets.map((p) => {
          const opt = document.createElement("option");
          opt.value = String(p.planet_id);
          opt.textContent = `[${p.coords}] ${p.name || "Colony"}`;
          return opt;
        })
      );
    }
    pSel.value = String(targetPid);
  }

  const chosenPlanet = planets.find((p) => p.planet_id === Number(pSel.value)) || activeP;
  const bMap = chosenPlanet?.buildings || {};
  const rMap = dash?.state?.researches || {};
  const sMap = chosenPlanet?.ships || {};

  if (cList && dash?.state?.raid_targets) {
    const coordsSet = new Set();
    for (const rt of dash.state.raid_targets) {
      const c = rt.coords || rt.target_coords;
      if (c) coordsSet.add(c);
    }
    const cSig = Array.from(coordsSet).slice(0, 40).join(",");
    if (cList.dataset.sig !== cSig) {
      cList.dataset.sig = cSig;
      cList.replaceChildren(
        ...Array.from(coordsSet)
          .slice(0, 40)
          .map((c) => {
            const opt = document.createElement("option");
            opt.value = c;
            return opt;
          })
      );
    }
  }

  const kind = String(kSel.value || "BUILD").toUpperCase();
  const prevTargetVal = uSel.value;

  if (kind === "BUILD" || kind === "DEMOLISH_BUILDING" || kind === "THROTTLE_BUILDING") {
    uWrap.hidden = false;
    cWrap.hidden = true;
    lWrap.hidden = false;
    uLbl.textContent = "Building";
    const items = kind === "THROTTLE_BUILDING" ? MQ_BUILDINGS_CATALOG.slice(0, 6) : MQ_BUILDINGS_CATALOG;
    uSel.replaceChildren(
      ...items.map((b) => {
        const opt = document.createElement("option");
        opt.value = String(b.id);
        const curL = finiteNum(bMap[`b${b.id}`], 0);
        opt.textContent = `${b.name} (L${curL})`;
        return opt;
      })
    );
    if (items.some((b) => String(b.id) === prevTargetVal)) {
      uSel.value = prevTargetVal;
    } else if (kind === "DEMOLISH_BUILDING") {
      uSel.value = "6";
    } else if (kind === "THROTTLE_BUILDING") {
      uSel.value = "2";
    }
    const selBid = Number(uSel.value || 1);
    const curL = finiteNum(bMap[`b${selBid}`], 0);
    if (kind === "BUILD") {
      lLbl.textContent = "Goal Lvl";
      lInp.min = "1";
      lInp.max = "200";
      lInp.step = "1";
      if (forceDefaultLevel || !lInp.value) lInp.value = String(curL + 1);
    } else if (kind === "DEMOLISH_BUILDING") {
      lLbl.textContent = "Down To Lvl";
      lInp.min = "0";
      lInp.max = "200";
      lInp.step = "1";
      if (forceDefaultLevel || !lInp.value) lInp.value = String(Math.max(0, curL - 1));
    } else {
      lLbl.textContent = "Rate %";
      lInp.min = "0";
      lInp.max = "100";
      lInp.step = "10";
      if (forceDefaultLevel || !lInp.value) lInp.value = "100";
    }
  } else if (kind === "RESEARCH") {
    uWrap.hidden = false;
    cWrap.hidden = true;
    lWrap.hidden = false;
    uLbl.textContent = "Research";
    uSel.replaceChildren(
      ...MQ_RESEARCH_CATALOG.map((r) => {
        const opt = document.createElement("option");
        opt.value = String(r.id);
        const curL = finiteNum(rMap[`r${r.id}`], 0);
        opt.textContent = `${r.name} (L${curL})`;
        return opt;
      })
    );
    if (MQ_RESEARCH_CATALOG.some((r) => String(r.id) === prevTargetVal)) {
      uSel.value = prevTargetVal;
    }
    const selRid = Number(uSel.value || 1);
    const curL = finiteNum(rMap[`r${selRid}`], 0);
    lLbl.textContent = "Goal Lvl";
    lInp.min = "1";
    lInp.max = "200";
    lInp.step = "1";
    if (forceDefaultLevel || !lInp.value) lInp.value = String(curL + 1);
  } else if (kind === "BUILD_SHIP") {
    uWrap.hidden = false;
    cWrap.hidden = true;
    lWrap.hidden = false;
    uLbl.textContent = "Ship Class";
    uSel.replaceChildren(
      ...MQ_SHIPS_CATALOG.map((s) => {
        const opt = document.createElement("option");
        opt.value = String(s.id);
        const owned = finiteNum(sMap[s.key], 0);
        opt.textContent = `${s.name} (${owned} owned)`;
        return opt;
      })
    );
    if (MQ_SHIPS_CATALOG.some((s) => String(s.id) === prevTargetVal)) {
      uSel.value = prevTargetVal;
    }
    lLbl.textContent = "Quantity";
    lInp.min = "1";
    lInp.max = "99999";
    lInp.step = "1";
    if (forceDefaultLevel || !lInp.value || Number(lInp.value) < 1) lInp.value = "1";
  } else if (kind === "SPY" || kind === "ATTACK" || kind === "RECYCLE" || kind === "COLONIZE") {
    uWrap.hidden = true;
    cWrap.hidden = false;
    cLbl.textContent = "Target Coords";
    cInp.placeholder = "02:381:12";
    const usesQty = kind === "SPY" || kind === "RECYCLE";
    lWrap.hidden = !usesQty;
    if (usesQty) {
      lLbl.textContent = kind === "SPY" ? "Probes" : "Recyclers";
      lInp.min = "1";
      lInp.max = "9999";
      lInp.step = "1";
      if (forceDefaultLevel || !lInp.value || Number(lInp.value) < 1) lInp.value = "1";
    }
  } else if (kind === "SCAN_GALAXY") {
    uWrap.hidden = true;
    cWrap.hidden = false;
    lWrap.hidden = true;
    cLbl.textContent = "System (GG:SSS)";
    const defSys = chosenPlanet?.coords ? chosenPlanet.coords.slice(0, 6) : "02:380";
    cInp.placeholder = defSys;
    if (forceDefaultLevel && !cInp.value) cInp.value = defSys;
  } else {
    uWrap.hidden = true;
    cWrap.hidden = true;
    lWrap.hidden = true;
  }
}

function actionPayloadFor(action, dryRun) {
  if (action.kind === "CLAIM_TUTORIAL") return { command: "claim_tutorial", dry_run: dryRun };
  if (action.kind === "BUILD" && action.target_id != null) {
    return { command: "build", building_id: action.target_id, planet_id: action.planet_id, dry_run: dryRun };
  }
  if (action.kind === "DEMOLISH_BUILDING" && action.target_id != null) {
    return { command: "demolish_building", building_id: action.target_id, planet_id: action.planet_id, dry_run: dryRun };
  }
  if (action.kind === "RESEARCH" && action.target_id != null) {
    return { command: "research", research_id: action.target_id, planet_id: action.planet_id, dry_run: dryRun };
  }
  if (action.kind === "BUILD_SHIP" && action.target_id != null) {
    return {
      command: "build_ship",
      ship_id: action.target_id,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "THROTTLE_BUILDING" && action.target_id != null) {
    return {
      command: "throttle_building",
      building_id: action.target_id,
      percentage: action.target_level == null ? 0 : finiteNum(action.target_level, 0),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "ESCAPE_FLIGHT") {
    return { command: "fleetsave_plan", planet_id: action.planet_id };
  }
  if (action.kind === "SCAN_GALAXY") {
    const m = /(\d{1,2}):(\d{1,3})/.exec(action.target_name || action.planet_coords || "");
    return {
      command: "scan_galaxy",
      galaxy: m ? Number(m[1]) : undefined,
      system: m ? Number(m[2]) : undefined,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "ATTACK") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "attack",
      target_coords: m ? m[1] : action.target_name,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "SPY") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "spy",
      target_coords: m ? m[1] : action.target_name,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "RECYCLE") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "recycle",
      target_coords: m ? m[1] : action.target_name,
      quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "COLONIZE") {
    const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
    return {
      command: "colonize",
      target_coords: m ? m[1] : action.target_name,
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  return null;
}

async function dispatchAction(action, dryRun) {
  const payload = actionPayloadFor(action, dryRun);
  if (!payload) return;
  try {
    showFeedback(`${dryRun ? "Simulating" : "Executing"} ${action.target_name}...`, "ok");
    const res = await apiCommand(payload);
    showFeedback(res.message || `Command ${payload.command} completed.`, "ok");
    await loadDashboard({ refresh: false, force: true });
  } catch (err) {
    showFeedback(`Action failed: ${err.message}`, feedbackKindForError(err));
  }
}

function buildPlanRow(act, { compact = false } = {}) {
  const tr = document.createElement("tr");
  const isManual = act.manual_queue_id != null && Number(act.manual_queue_id) > 0;
  if (isManual) {
    tr.classList.add("is-manual-queued");
  }

  const tdPrio = document.createElement("td");
  const prioWrap = document.createElement("div");
  prioWrap.className = "prio-cell-stack";
  const prioTag = document.createElement("span");
  const prioVal = String(act.priority || "?");
  prioTag.className = `prio-tag prio-${prioVal}`;
  const prioDot = document.createElement("span");
  prioDot.className = "prio-dot";
  prioTag.append(prioDot, document.createTextNode(prioVal));
  prioWrap.appendChild(prioTag);
  if (isManual) {
    const pinBadge = document.createElement("span");
    pinBadge.className = "mq-pin-badge tabular";
    pinBadge.textContent = `PIN #${act.manual_queue_id}`;
    prioWrap.appendChild(pinBadge);
  }
  tdPrio.appendChild(prioWrap);
  tr.appendChild(tdPrio);

  const tdTarget = document.createElement("td");
  let lvlStr = "";
  if (act.kind === "THROTTLE_BUILDING" && act.target_level != null) {
    lvlStr = ` (${act.target_level}%)`;
  } else if (act.kind === "BUILD_SHIP" && act.target_level) {
    lvlStr = ` x${act.target_level}`;
  } else if (act.target_level != null) {
    lvlStr = ` → L${act.target_level}`;
  }
  const titleDiv = document.createElement("div");
  titleDiv.className = "action-title";
  titleDiv.textContent = `${act.target_name || "Action"}${lvlStr}`;
  const subDiv = document.createElement("div");
  subDiv.className = "action-sub";
  subDiv.textContent = `${act.kind || "ACTION"} · [${act.planet_coords || "—"}]`;
  tdTarget.append(titleDiv, subDiv);
  tr.appendChild(tdTarget);

  const tdCost = document.createElement("td");
  tdCost.className = "tabular";
  const costDiv = document.createElement("div");
  costDiv.textContent = formatResourcesInline(act.cost);
  tdCost.appendChild(costDiv);
  const missStr = formatResourcesInline(act.missing);
  if (missStr !== "0" && missStr !== "—") {
    const missDiv = document.createElement("div");
    missDiv.className = "action-sub";
    missDiv.textContent = `Missing: ${missStr}`;
    tdCost.appendChild(missDiv);
  }
  tr.appendChild(tdCost);

  const hasRawWait =
    act.wait_eta !== undefined && act.wait_eta !== null && String(act.wait_eta).trim() !== "";
  const isBlocked =
    (hasRawWait && String(act.wait_eta) === "—") ||
    act.wait_hours === null ||
    (!hasRawWait &&
      !act.executable_now &&
      (act.wait_hours == null ||
        !Number.isFinite(Number(act.wait_hours)) ||
        Number(act.wait_hours) <= 0));
  const waitHoursNum =
    act.wait_hours !== undefined &&
    act.wait_hours !== null &&
    Number.isFinite(Number(act.wait_hours))
      ? Number(act.wait_hours)
      : 0;
  const waitEta = hasRawWait
    ? String(act.wait_eta)
    : isBlocked
      ? "—"
      : act.executable_now
        ? "Ready"
        : "—";
  const bnTag = bottleneckTag(act.eta_bottleneck);
  const isWaiting =
    !isBlocked &&
    (waitHoursNum > 0 || (waitEta !== "Ready" && waitEta !== "Instant" && waitEta !== "—"));

  const tdWait = document.createElement("td");
  tdWait.className = "col-right tabular";
  const waitPill = document.createElement("span");
  waitPill.className = `eta-pill${isBlocked ? " is-blocked" : isWaiting ? " is-wait" : ""}`;
  waitPill.textContent = (isBlocked || isWaiting) && bnTag ? `${waitEta} (${bnTag})` : waitEta;
  if (bnTag) {
    waitPill.setAttribute("title", `Bottleneck: ${bnTag}`);
  }
  tdWait.appendChild(waitPill);
  tr.appendChild(tdWait);

  const hasRawTotal =
    act.total_eta !== undefined && act.total_eta !== null && String(act.total_eta).trim() !== "";
  const hasRawDur =
    act.duration_text !== undefined &&
    act.duration_text !== null &&
    String(act.duration_text).trim() !== "";
  const durText = hasRawDur ? String(act.duration_text) : "";
  const totalBlocked =
    isBlocked || (hasRawTotal && String(act.total_eta) === "—") || act.total_hours === null;
  const totalEta = hasRawTotal
    ? String(act.total_eta)
    : totalBlocked
      ? "—"
      : durText || (act.executable_now ? "Ready" : "—");

  const tdTotal = document.createElement("td");
  tdTotal.className = "col-right tabular";
  const totalPill = document.createElement("span");
  totalPill.className = `eta-pill${totalBlocked ? " is-blocked" : isWaiting ? " is-wait" : ""}`;
  totalPill.textContent = totalEta;
  tdTotal.appendChild(totalPill);
  const totSecNum = finiteNum(act.total_seconds, null);
  if (compact && !totalBlocked && totSecNum !== null && totSecNum > 0) {
    const finAtDiv = document.createElement("div");
    finAtDiv.className = "finish-clock-sub tabular";
    finAtDiv.textContent = `at ${formatFinishClock(Date.now() / 1000 + totSecNum)}`;
    tdTotal.appendChild(finAtDiv);
  } else if (!compact && durText && durText !== totalEta) {
    const durDiv = document.createElement("div");
    durDiv.className = "action-sub";
    durDiv.textContent = durText === "Instant" ? "Instant" : `Dur: ${durText}`;
    tdTotal.appendChild(durDiv);
  }
  tr.appendChild(tdTotal);

  if (!compact) {
    const tdReason = document.createElement("td");
    tdReason.textContent = act.reason || "";
    tr.appendChild(tdReason);
  }

  const tdOps = document.createElement("td");
  tdOps.className = "col-right";
  const cmdTemplate = actionPayloadFor(act, false);
  const opsWrap = document.createElement("div");
  opsWrap.className = "row-actions-wrap";

  if (cmdTemplate && act.executable_now) {
    const runBtn = document.createElement("button");
    runBtn.type = "button";
    runBtn.className = "btn btn-primary btn-sm";
    runBtn.textContent = "Run";
    runBtn.addEventListener("click", () => dispatchAction(act, false));

    const dryBtn = document.createElement("button");
    dryBtn.type = "button";
    dryBtn.className = "btn btn-secondary btn-sm";
    dryBtn.textContent = "Sim";
    dryBtn.addEventListener("click", () => dispatchAction(act, true));

    opsWrap.append(runBtn, dryBtn);
  } else if (!isManual) {
    const waitTag = document.createElement("span");
    waitTag.className = "action-sub";
    waitTag.textContent = "Waiting";
    opsWrap.appendChild(waitTag);
  }

  if (isManual) {
    const qid = Number(act.manual_queue_id);
    const targetLabel = String(act.target_name || "command");
    if (!compact) {
      const prioSel = document.createElement("select");
      prioSel.className = "mq-prio-select";
      prioSel.setAttribute("aria-label", `Priority for ${targetLabel} (#${qid})`);
      for (const pOpt of ["P0", "P1", "P2", "P3"]) {
        const o = document.createElement("option");
        o.value = pOpt;
        o.textContent = pOpt;
        if (pOpt === prioVal) o.selected = true;
        prioSel.appendChild(o);
      }
      prioSel.addEventListener("change", () => {
        updateManualQueueItem(qid, { priority: prioSel.value, move: "top" });
      });
      opsWrap.appendChild(prioSel);

      const topBtn = document.createElement("button");
      topBtn.type = "button";
      topBtn.className = "btn btn-secondary btn-sm";
      topBtn.textContent = "Top";
      topBtn.title = "Move to top of priority band";
      topBtn.setAttribute("aria-label", `Move ${targetLabel} (#${qid}) to top of ${prioVal}`);
      topBtn.addEventListener("click", () => updateManualQueueItem(qid, { move: "top" }));
      opsWrap.appendChild(topBtn);
    }

    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "btn btn-secondary btn-sm";
    upBtn.textContent = "Up";
    upBtn.title = "Move up in queue";
    upBtn.setAttribute("aria-label", `Move ${targetLabel} (#${qid}) up in queue`);
    upBtn.addEventListener("click", () => updateManualQueueItem(qid, { move: "up" }));

    const dnBtn = document.createElement("button");
    dnBtn.type = "button";
    dnBtn.className = "btn btn-secondary btn-sm";
    dnBtn.textContent = "Dn";
    dnBtn.title = "Move down in queue";
    dnBtn.setAttribute("aria-label", `Move ${targetLabel} (#${qid}) down in queue`);
    dnBtn.addEventListener("click", () => updateManualQueueItem(qid, { move: "down" }));

    const dropBtn = document.createElement("button");
    dropBtn.type = "button";
    dropBtn.className = "btn btn-secondary btn-sm btn-danger-ghost";
    dropBtn.textContent = "Drop";
    dropBtn.title = "Remove from manual queue";
    dropBtn.setAttribute("aria-label", `Remove ${targetLabel} (#${qid}) from manual queue`);
    dropBtn.addEventListener("click", () => cancelManualQueueItem(qid));

    if (compact) {
      opsWrap.append(upBtn, dropBtn);
    } else {
      opsWrap.append(upBtn, dnBtn, dropBtn);
    }
  } else if (actionPayloadFor(act.kind === "WAIT_RESOURCES" ? { ...act, kind: "BUILD" } : act, true)) {
    const pinBtn = document.createElement("button");
    pinBtn.type = "button";
    pinBtn.className = "btn btn-secondary btn-sm";
    pinBtn.textContent = "Pin P1";
    pinBtn.title = "Pin & prioritize at top of P1 queue";
    pinBtn.setAttribute("aria-label", `Pin ${act.target_name || "action"} to P1 queue`);
    pinBtn.addEventListener("click", () => pinPlannedActionToQueue(act, "P1"));
    opsWrap.appendChild(pinBtn);
  }

  tdOps.appendChild(opsWrap);
  tr.appendChild(tdOps);
  return tr;
}

const DASH_FLEET_KINDS = new Set([
  "BUILD_SHIP",
  "SHIP",
  "BUILD_DEFENSE",
  "DEFENSE",
  "SPY",
  "ATTACK",
  "RECYCLE",
  "COLONIZE",
  "SCAN_GALAXY",
  "ESCAPE_FLIGHT",
  "ALERT_HOSTILE",
]);

function dashboardQueueDomainForAction(act) {
  const kind = String(act?.kind || "").toUpperCase();
  if (kind === "RESEARCH") return "research";
  if (DASH_FLEET_KINDS.has(kind)) return "fleet";
  return "building";
}

function pad2Digits(n) {
  return String(Math.max(0, Math.trunc(n))).padStart(2, "0");
}

function formatCountdownHms(seconds) {
  const n = finiteNum(seconds, null);
  if (n === null) return "—";
  const s = Math.max(0, Math.round(n));
  if (s <= 0) return "00:00:00";
  if (s >= 86400) {
    const d = Math.floor(s / 86400);
    const rem = s % 86400;
    const h = Math.floor(rem / 3600);
    const m = Math.floor((rem % 3600) / 60);
    const sec = rem % 60;
    return `${d}d ${pad2Digits(h)}:${pad2Digits(m)}:${pad2Digits(sec)}`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${pad2Digits(h)}:${pad2Digits(m)}:${pad2Digits(sec)}`;
}

function formatFinishClock(finishTs) {
  const ts = finiteNum(finishTs, null);
  if (ts === null || ts <= 0) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  const now = new Date();
  const hms = `${pad2Digits(d.getHours())}:${pad2Digits(d.getMinutes())}:${pad2Digits(d.getSeconds())}`;
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return hms;
  return `${pad2Digits(d.getMonth() + 1)}-${pad2Digits(d.getDate())} ${hms}`;
}

function cleanQueueJobTitle(job) {
  const rawName = String(job?.name || "Active job")
    .replace(/^(?:cancel|abort)\s+/i, "")
    .replace(/\s*\(\s*Level\s+\d+\s*\)\s*$/i, "")
    .trim();
  const lvl = finiteNum(job?.target_level, null);
  if (lvl !== null && lvl > 0) {
    return `${rawName} → L${lvl}`;
  }
  return rawName || "Active job";
}

function dashboardQueueDomainForJob(job) {
  const cat = String(job?.category || "").toLowerCase();
  if (cat === "research") return "research";
  if (cat === "fleet" || cat === "shipyard" || cat === "defense") return "fleet";
  return "building";
}

function buildActiveExecRow(job) {
  const tr = document.createElement("tr");
  const slot = Math.max(1, Math.trunc(finiteNum(job?.queue_slot, 1) || 1));
  tr.className = slot > 1 ? "dash-exec-row is-queued-slot" : "dash-exec-row";

  const tdPrio = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = slot > 1 ? "prio-badge prio-game-q tabular" : "prio-badge prio-exec tabular";
  badge.textContent = slot > 1 ? `GAME #${slot}` : "EXEC";
  tdPrio.appendChild(badge);

  const tdAction = document.createElement("td");
  const titleDiv = document.createElement("div");
  titleDiv.className = "action-title";
  titleDiv.textContent = cleanQueueJobTitle(job);
  tdAction.appendChild(titleDiv);

  const tdDetail = document.createElement("td");
  const detSpan = document.createElement("span");
  detSpan.className = "action-sub";
  detSpan.textContent =
    String(job?.detail || "").trim() ||
    (slot > 1 ? `In-game queue #${slot}` : "Executing now");
  tdDetail.appendChild(detSpan);

  const finTs = finiteNum(job?.finish_ts, null);
  const remSec =
    finTs !== null && finTs > 0
      ? Math.max(0, Math.round(finTs - Date.now() / 1000))
      : finiteNum(job?.remaining_seconds, null);

  const tdRem = document.createElement("td");
  tdRem.className = "col-right tabular";
  const remPill = document.createElement("span");
  remPill.className = "eta-pill is-exec-timer";
  if (finTs !== null && finTs > 0) {
    remPill.dataset.finishTs = String(finTs);
    remPill.textContent = formatCountdownHms(remSec);
  } else {
    remPill.textContent = String(job?.remaining_text || "").trim() || "Running";
  }
  tdRem.appendChild(remPill);

  const tdFinish = document.createElement("td");
  tdFinish.className = "col-right tabular";
  const finPill = document.createElement("span");
  finPill.className = "eta-pill is-finish-at";
  finPill.textContent = finTs !== null && finTs > 0 ? formatFinishClock(finTs) : "In progress";
  tdFinish.appendChild(finPill);

  const tdOps = document.createElement("td");
  tdOps.className = "col-right";
  const liveTag = document.createElement("span");
  liveTag.className = slot > 1 ? "exec-live-label is-slot" : "exec-live-label";
  const detLow = String(job?.detail || "").toLowerCase();
  if (slot > 1) {
    liveTag.textContent = `Queued #${slot}`;
  } else if (detLow.includes("returning")) {
    liveTag.textContent = "Returning";
  } else if (detLow.includes("outbound") || detLow.includes("flight")) {
    liveTag.textContent = "In flight";
  } else {
    liveTag.textContent = "Executing";
  }
  tdOps.appendChild(liveTag);

  tr.append(tdPrio, tdAction, tdDetail, tdRem, tdFinish, tdOps);
  return tr;
}

function tickLiveQueueCountdowns() {
  const nodes = document.querySelectorAll("[data-finish-ts]");
  if (!nodes.length) return;
  const nowSec = Date.now() / 1000;
  nodes.forEach((node) => {
    const finTs = finiteNum(node.dataset.finishTs, null);
    if (finTs === null || finTs <= 0) return;
    const rem = Math.max(0, Math.round(finTs - nowSec));
    node.textContent = rem > 0 ? formatCountdownHms(rem) : "Finishing...";
  });
}

function renderDashQueueSlice(execTbodyEl, tbodyEl, countEl, execJobs, domainActions, emptyText) {
  const activeList = Array.isArray(execJobs) ? execJobs : [];
  if (countEl) {
    countEl.textContent = String(activeList.length + domainActions.length);
  }
  if (execTbodyEl) {
    if (activeList.length === 0) {
      execTbodyEl.hidden = true;
      execTbodyEl.replaceChildren();
    } else {
      execTbodyEl.hidden = false;
      execTbodyEl.replaceChildren(...activeList.map((j) => buildActiveExecRow(j)));
    }
  }
  if (!tbodyEl) return;
  const topSlice = domainActions.slice(0, 10);
  if (topSlice.length === 0) {
    if (activeList.length > 0) {
      tbodyEl.replaceChildren();
      return;
    }
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "empty-cell";
    td.textContent = emptyText;
    tr.appendChild(td);
    tbodyEl.replaceChildren(tr);
  } else {
    tbodyEl.replaceChildren(...topSlice.map((a) => buildPlanRow(a, { compact: true })));
  }
}

function renderPlanTable(dash) {
  const tbody = el("plan-tbody");
  const allActions = dash.plan || [];
  const manualList = Array.isArray(dash.manual_queue)
    ? dash.manual_queue
    : allActions.filter((a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0);
  const pinnedCount = manualList.length;

  const dashPinnedBadge = el("dash-pinned-count-badge");
  if (dashPinnedBadge) {
    dashPinnedBadge.hidden = pinnedCount === 0;
    dashPinnedBadge.textContent = `${pinnedCount} PINNED`;
  }
  const opsPinnedBadge = el("ops-pinned-badge");
  if (opsPinnedBadge) {
    opsPinnedBadge.hidden = pinnedCount === 0;
    opsPinnedBadge.textContent = `${pinnedCount} PINNED`;
  }
  const manualChip = el("chip-filter-manual");
  if (manualChip) {
    manualChip.textContent = `Pinned Only (${pinnedCount})`;
  }
  const clearBtn = el("btn-mq-clear");
  if (clearBtn) {
    clearBtn.hidden = pinnedCount === 0;
    clearBtn.textContent = `Clear Pinned (${pinnedCount})`;
  }
  syncManualQueueComposer(dash);

  let actions = allActions;
  if (state.queueFilter === "ready") {
    actions = allActions.filter((a) => a.executable_now);
  } else if (state.queueFilter === "manual") {
    actions = allActions.filter((a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0);
  }
  el("plan-count-badge").textContent = `${actions.length} TASKS`;
  const navCount = el("nav-queue-count");
  if (navCount) navCount.textContent = String(allActions.length);

  if (actions.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.className = "empty-cell";
    td.textContent = "No matching actions in queue.";
    tr.appendChild(td);
    tbody.replaceChildren(tr);
  } else {
    tbody.replaceChildren(...actions.map((a) => buildPlanRow(a, { compact: false })));
  }

  const activeQueues = Array.isArray(dash.state?.active_queues) ? dash.state.active_queues : [];
  const execBuildingJobs = [];
  const execFleetJobs = [];
  const execResearchJobs = [];
  for (const job of activeQueues) {
    const jDom = dashboardQueueDomainForJob(job);
    if (jDom === "research") {
      execResearchJobs.push(job);
    } else if (jDom === "fleet") {
      execFleetJobs.push(job);
    } else {
      execBuildingJobs.push(job);
    }
  }

  const buildingActions = [];
  const fleetActions = [];
  const researchActions = [];
  for (const act of allActions) {
    const domain = dashboardQueueDomainForAction(act);
    if (domain === "research") {
      researchActions.push(act);
    } else if (domain === "fleet") {
      fleetActions.push(act);
    } else {
      buildingActions.push(act);
    }
  }

  renderDashQueueSlice(
    el("dash-exec-building-tbody"),
    el("dash-plan-tbody"),
    el("dash-bld-q-count"),
    execBuildingJobs,
    buildingActions,
    "No queued building actions."
  );
  renderDashQueueSlice(
    el("dash-exec-fleet-tbody"),
    el("dash-plan-fleet-tbody"),
    el("dash-fleet-q-count"),
    execFleetJobs,
    fleetActions,
    "No queued fleet or ship actions."
  );
  renderDashQueueSlice(
    el("dash-exec-research-tbody"),
    el("dash-plan-research-tbody"),
    el("dash-research-q-count"),
    execResearchJobs,
    researchActions,
    "No queued research actions."
  );
}

function renderDashNearestEtas(dash) {
  const body = el("dash-eta-tbody");
  if (!body) return;
  const eta = currentBuildingsEta(dash);
  const blds = eta?.buildings || [];
  if (blds.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "empty-cell";
    td.textContent = "No building forecast data yet.";
    tr.appendChild(td);
    body.replaceChildren(tr);
    return;
  }

  const prioritized = [...blds]
    .filter((b) => (b.unmet_prerequisites || []).length === 0)
    .sort((a, b) => {
      const ra = finiteNum(a.reach_hours, 1e9);
      const rb = finiteNum(b.reach_hours, 1e9);
      return ra - rb;
    })
    .slice(0, 6);

  body.replaceChildren(
    ...prioritized.map((b) => {
      const tr = document.createElement("tr");
      tr.className = "clickable-row";
      tr.addEventListener("click", () => {
        state.selectedUnitKey = b.key;
        state.bldCategory = "all";
        setActivePage("buildings");
        renderBuildingsView(state.dashboard);
      });

      const tdN = document.createElement("td");
      tdN.textContent = b.name || "Building";
      const tdNext = document.createElement("td");
      tdNext.className = "tabular";
      tdNext.textContent = `L${b.current_level ?? 0} → L${b.next_level ?? b.target_level ?? "—"}`;
      const tdTime = document.createElement("td");
      tdTime.className = "tabular";
      tdTime.textContent = b.build_time_text ?? "—";
      const tdReach = document.createElement("td");
      tdReach.className = "col-right tabular";
      const pill = document.createElement("span");
      const reachEta = b.reach_eta ?? "—";
      const waitH = finiteNum(b.wait_hours, 0);
      pill.className = `eta-pill${reachEta === "—" ? " is-blocked" : waitH > 0 ? " is-wait" : ""}`;
      pill.textContent = reachEta;
      tdReach.appendChild(pill);

      tr.append(tdN, tdNext, tdTime, tdReach);
      return tr;
    })
  );
}

function bottleneckTag(code) {
  if (code == null) return "";
  const norm = String(code).trim().toLowerCase();
  if (!norm) return "";
  if (norm === "pig_iron" || norm === "pig") return "PIG";
  if (norm === "metal" || norm === "met") return "MET";
  if (norm === "kryptonite" || norm === "kryp" || norm === "kry") return "KRY";
  if (norm === "spice" || norm === "spi") return "SPI";
  return norm.toUpperCase();
}

function finiteNum(val, fallback = null) {
  if (
    val === null ||
    val === undefined ||
    typeof val === "boolean" ||
    typeof val === "object" ||
    typeof val === "symbol" ||
    (typeof val === "string" && val.trim() === "")
  ) {
    return fallback;
  }
  const n = Number(val);
  return Number.isFinite(n) ? n : fallback;
}

function clampPct(val) {
  const n = finiteNum(val, 0);
  return Math.max(0, Math.min(100, n));
}

function fmtCompact(val) {
  const n = finiteNum(val, null);
  if (n === null) return "—";
  const absN = Math.abs(n);
  if (absN >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (absN >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  if (Number.isInteger(n)) return fmtInt(n);
  return absN < 0.05 ? "0.0" : n.toFixed(1);
}

function fmtImprovementPct(rawPct, baseFit, bestFit) {
  let pct = finiteNum(rawPct, null);
  const base = finiteNum(baseFit, null);
  const best = finiteNum(bestFit, null);
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

function geneShortTag(kind) {
  const k = String(kind || "").trim().toUpperCase();
  if (!k) return "";
  if (k === "BUILD") return "BLD";
  if (k === "DEMOLISH_BUILDING") return "DEM";
  if (k === "WAIT_RESOURCES") return "WAIT";
  if (k === "RESEARCH") return "RES";
  if (k === "BUILD_SHIP") return "SHP";
  if (k === "ATTACK") return "ATK";
  if (k === "SPY") return "SPY";
  if (k === "RECYCLE") return "REC";
  if (k === "COLONIZE") return "COL";
  if (k === "SCAN_GALAXY") return "SCN";
  if (k === "THROTTLE_BUILDING") return "B2%";
  if (k === "CLAIM_TUTORIAL") return "TUT";
  return k.slice(0, 4);
}

function renderGaNnHud(dash) {
  const cfgMode = String(dash?.config?.planner_mode || dash?.planner_mode || "priority").toLowerCase();
  const isGaNn = cfgMode === "ga_nn";
  const modeLabel = isGaNn ? "MODE: GA+NN" : "MODE: PRIORITY";

  for (const badgeId of ["dash-planner-mode-badge", "ops-planner-mode-badge"]) {
    const badge = el(badgeId);
    if (badge) {
      badge.textContent = modeLabel;
      badge.classList.toggle("is-ga", isGaNn);
    }
  }
  const navGraphBadge = el("nav-graph-badge");
  if (navGraphBadge) {
    navGraphBadge.textContent = isGaNn ? "GA" : "PRI";
    navGraphBadge.classList.toggle("is-ga", isGaNn);
  }

  const hud = el("gann-calc-hud");
  if (!hud) return;

  hud.hidden = !isGaNn;
  hud.classList.toggle("is-active", isGaNn);
  hud.classList.toggle("is-calculating", isGaNn && Boolean(state.gaCalcRunning));

  if (!isGaNn) {
    state.lastOptimizerSummary = null;
    return;
  }

  const incomingSum =
    dash?.optimizer_summary || dash?.ga_nn_telemetry || dash?.optimizer_metrics || null;
  const hasIncoming = Boolean(
    incomingSum && typeof incomingSum === "object" && Object.keys(incomingSum).length > 0
  );
  if (hasIncoming) {
    state.lastOptimizerSummary = incomingSum;
  }
  const rawSum = (hasIncoming ? incomingSum : state.lastOptimizerSummary) || {};
  const hasSummary = Boolean(
    rawSum && typeof rawSum === "object" && Object.keys(rawSum).length > 0
  );

  const cfgPop = Math.max(4, Math.round(finiteNum(dash?.config?.ga_population_size, 24)));
  const cfgGens = Math.max(1, Math.round(finiteNum(dash?.config?.ga_generations, 12)));
  const horizonSteps = Math.max(1, Math.round(finiteNum(dash?.config?.ga_horizon_steps, 6)));

  const statusPill = el("gann-status-pill");
  if (statusPill) {
    statusPill.textContent = state.gaCalcRunning
      ? "CALCULATING"
      : hasSummary
        ? "OPTIMIZED"
        : "ACTIVE";
  }

  const popSize = Math.max(1, Math.round(finiteNum(rawSum.population_size, cfgPop)));
  const nnBackend = String(rawSum.nn_backend || "mlp").toUpperCase();
  const elapsedMs = finiteNum(rawSum.elapsed_ms, null);
  const safeElapsed = elapsedMs !== null ? Math.max(0, Math.round(elapsedMs)) : null;
  const metaLine = el("gann-meta-line");
  if (metaLine) {
    metaLine.textContent =
      safeElapsed !== null
        ? `POP ${popSize} · ${nnBackend} · ${safeElapsed}ms`
        : `POP ${popSize} · ${nnBackend}`;
  }

  const histLen = Array.isArray(rawSum.generation_history) ? rawSum.generation_history.length : null;
  const genDone = Math.max(
    0,
    Math.round(finiteNum(rawSum.generation ?? rawSum.generations ?? histLen, 0))
  );
  const genTarget = Math.max(genDone, cfgGens);
  const genPct = clampPct(genTarget > 0 ? (genDone / genTarget) * 100 : 0);
  if (el("gann-gen-val")) el("gann-gen-val").textContent = `${genDone} / ${genTarget}`;
  if (el("gann-gen-sub")) {
    const seedVal = finiteNum(rawSum.seed ?? dash?.config?.ga_seed, null);
    const seedTxt = seedVal !== null ? `Seed ${Math.round(seedVal)}` : "Auto seed";
    el("gann-gen-sub").textContent = state.gaCalcRunning
      ? "Evolving action sequences..."
      : `Horizon ${horizonSteps} steps · ${seedTxt}`;
  }
  el("gann-arc-gen")?.style.setProperty("--arc-pct", String(genPct));
  if (el("gann-gen-bar")) el("gann-gen-bar").style.transform = `scaleX(${genPct / 100})`;

  const nnEvals = Math.max(
    0,
    Math.round(finiteNum(rawSum.nn_evaluations ?? rawSum.neural_evals, 0))
  );
  const lossRaw = finiteNum(rawSum.nn_final_loss, null);
  const loss = lossRaw !== null ? Math.max(0, lossRaw) : null;
  const nnPct = state.gaCalcRunning
    ? 72
    : nnEvals > 0
      ? clampPct(Math.max(14, Math.log10(nnEvals + 1) * 38))
      : 0;
  if (el("gann-nn-val")) el("gann-nn-val").textContent = `${fmtInt(nnEvals)} evals`;
  if (el("gann-nn-sub")) {
    el("gann-nn-sub").textContent =
      loss !== null
        ? `MSE Loss ${loss.toFixed(4)} · ${nnBackend}`
        : `Backend ${nnBackend} · Horizon ${horizonSteps}`;
  }
  el("gann-arc-nn")?.style.setProperty("--arc-pct", String(nnPct));
  if (el("gann-nn-bar")) el("gann-nn-bar").style.transform = `scaleX(${nnPct / 100})`;

  const baseFit = finiteNum(rawSum.baseline_fitness ?? rawSum.initial_best_fitness, null);
  const bestFit = finiteNum(
    rawSum.best_fitness ?? rawSum.fitness_score ?? rawSum.final_best_fitness,
    null
  );
  const impText = fmtImprovementPct(rawSum.improvement_pct, baseFit, bestFit);
  const rawImp = finiteNum(rawSum.improvement_pct, null);
  const fitPct =
    impText.startsWith(">")
      ? 100
      : clampPct(
          rawImp !== null
            ? Math.max(0, Math.min(100, rawImp + 15))
            : bestFit !== null && bestFit > 0
              ? 35
              : 0
        );
  if (el("gann-fit-val")) el("gann-fit-val").textContent = impText;
  if (el("gann-fit-sub")) {
    el("gann-fit-sub").textContent = `Base ${fmtCompact(baseFit)} · Best ${fmtCompact(bestFit)}`;
  }
  el("gann-arc-fit")?.style.setProperty("--arc-pct", String(fitPct));
  if (el("gann-fit-bar")) el("gann-fit-bar").style.transform = `scaleX(${fitPct / 100})`;

  const chrom = Array.isArray(rawSum.best_chromosome) ? rawSum.best_chromosome : [];
  const planLen = Array.isArray(dash?.plan) ? dash.plan.length : 0;
  const gLen = Math.max(
    0,
    Math.round(finiteNum(rawSum.genome_length ?? rawSum.actions_count, chrom.length || planLen))
  );
  const geneTags = chrom
    .slice(0, 5)
    .map((g) => geneShortTag(g?.kind))
    .filter(Boolean)
    .join(" · ");
  const gPct = clampPct(
    horizonSteps > 0 ? (Math.min(gLen, horizonSteps) / horizonSteps) * 100 : gLen > 0 ? 50 : 0
  );
  if (el("gann-genome-val")) {
    el("gann-genome-val").textContent = `${gLen} ${gLen === 1 ? "gene" : "genes"}`;
  }
  if (el("gann-genome-sub")) {
    el("gann-genome-sub").textContent =
      geneTags || (gLen > 0 ? `${gLen} queued steps` : "Sequence standby");
  }
  el("gann-arc-genome")?.style.setProperty("--arc-pct", String(gPct));
  if (el("gann-genome-bar")) el("gann-genome-bar").style.transform = `scaleX(${gPct / 100})`;
}

function renderBuildingInspector(etaData) {
  const rows = [...(etaData?.buildings || []), ...(etaData?.researches || [])];
  let selected = rows.find((r) => r.key === state.selectedUnitKey);
  if (!selected) {
    selected = (etaData?.buildings || [])[0] || (etaData?.researches || [])[0] || null;
    if (selected) state.selectedUnitKey = selected.key;
  }

  const horizonsBody = el("insp-horizons-tbody");
  const metaBox = el("insp-meta-box");
  const bfHint = el("insp-bf-hint");
  const buildBtn = el("btn-insp-build");

  if (!selected) {
    el("insp-key").textContent = "—";
    el("insp-title").textContent = "Select a Building or Tech";
    el("insp-subtitle").textContent = "Click any row on the left.";
    metaBox.textContent = "No data.";
    bfHint.hidden = true;
    buildBtn.hidden = true;
    horizonsBody.replaceChildren();
    return;
  }

  const isResearch = String(selected.key).startsWith("r");
  el("insp-key").textContent = String(selected.key).toUpperCase();
  el("insp-title").textContent = selected.name;
  el("insp-subtitle").textContent = `Current L${selected.current_level} · Next L${selected.next_level} · Build time ${selected.build_time_text}`;

  const unmet = selected.unmet_prerequisites || [];
  const pEcon = etaData?.planet_economy || {};
  const selBnTag =
    typeof selected.bottleneck === "string" && selected.bottleneck.trim()
      ? bottleneckTag(selected.bottleneck)
      : "";
  const parts = [
    `Prerequisites: ${unmet.length === 0 ? "Unlocked" : unmet.join(", ")}`,
    `Next L${selected.next_level} Cost: ${formatResourcesInline(selected.cost)}`,
    `Resources Ready: ${selected.wait_eta}${selBnTag ? ` (${selBnTag} bottleneck)` : ""}`,
    `Estimated Level Reached: ${selected.reach_eta}`,
  ];
  if (!isResearch && selected.energy_delta) {
    parts.push(`Energy Impact: ${selected.energy_delta >= 0 ? "+" : ""}${selected.energy_delta} E`);
  }
  metaBox.replaceChildren(
    ...parts.map((line) => {
      const s = document.createElement("span");
      s.textContent = line;
      return s;
    })
  );

  // Blast furnace 0% counter-factual impact note when Pig iron slows down upgrade
  const bfUsed = finiteNum(pEcon.blast_furnace_pig_used_per_hr, 0);
  const bfPct = finiteNum(pEcon.blast_furnace_pct, 100);
  const missPig = finiteNum(selected.missing?.pig_iron, 0);
  if (selected.bottleneck === "pig_iron" && bfPct > 0 && bfUsed > 0 && missPig > 0) {
    const newRate = finiteNum(pEcon.pig_iron_per_hr, 0) + bfUsed;
    const oldWait = selected.wait_eta;
    const altHours = newRate > 0 ? missPig / newRate : NaN;
    const altWait = Number.isFinite(altHours) ? `${altHours.toFixed(1)}h` : "Blocked";
    bfHint.textContent = `Bucket tip: Pausing Blast furnace (b2 → 0%) frees +${fmtInt(Math.round(bfUsed))} Pig/h (Pig iron wait improves from ${oldWait} to ${altWait}).`;
    bfHint.hidden = false;
  } else {
    bfHint.hidden = true;
  }

  const horizons = selected.horizons || [];
  if (horizons.length === 0) {
    horizonsBody.replaceChildren();
  } else {
    horizonsBody.replaceChildren(
      ...horizons.map((h) => {
        const tr = document.createElement("tr");
        const tdL = document.createElement("td");
        tdL.className = "tabular";
        tdL.textContent = `L${h.level}`;

        const tdC = document.createElement("td");
        tdC.className = "tabular";
        tdC.textContent = formatResourcesInline(h.cost);

        const tdB = document.createElement("td");
        tdB.className = "col-right tabular";
        tdB.textContent = h.build_time_text;

        const tdR = document.createElement("td");
        tdR.className = "col-right tabular";
        const span = document.createElement("span");
        span.className = `eta-pill${h.reach_eta === "—" ? " is-blocked" : ""}`;
        span.textContent = h.reach_eta;
        tdR.appendChild(span);

        tr.append(tdL, tdC, tdB, tdR);
        return tr;
      })
    );
  }

  const canQueue = isResearch ? selected.can_research_live : selected.can_build_live;
  if (canQueue) {
    buildBtn.hidden = false;
    buildBtn.textContent = isResearch
      ? `Start Research L${selected.next_level}`
      : `Queue L${selected.next_level} Live`;
    buildBtn.onclick = async () => {
      try {
        showFeedback(`Submitting ${selected.name} L${selected.next_level}...`, "ok");
        const cmd = isResearch
          ? { command: "research", research_id: selected.research_id, planet_id: etaData.planet?.planet_id, dry_run: false }
          : { command: "build", building_id: selected.building_id, planet_id: etaData.planet?.planet_id, dry_run: false };
        const res = await apiCommand(cmd);
        showFeedback(res.message || "Queued.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Build failed: ${err.message}`, feedbackKindForError(err));
      }
    };
  } else {
    buildBtn.hidden = true;
  }

  const pinQueueBtn = el("btn-insp-pin-queue");
  if (pinQueueBtn) {
    const nextLvl = finiteNum(selected.next_level ?? selected.target_level, null);
    const unitId = isResearch ? selected.research_id : selected.building_id;
    if (unitId != null && nextLvl !== null && nextLvl > 0) {
      pinQueueBtn.hidden = false;
      pinQueueBtn.textContent = `+ Pin L${nextLvl} to Queue (P1)`;
      pinQueueBtn.onclick = () => {
        enqueueManualCommand({
          kind: isResearch ? "RESEARCH" : "BUILD",
          priority: "P1",
          position: "top",
          planet_id: etaData.planet?.planet_id || 0,
          target_id: unitId,
          target_name: selected.name || "",
          target_level: nextLvl,
          deduplicate_pending: true,
        });
      };
    } else {
      pinQueueBtn.hidden = true;
    }
  }
}

function renderBuildingsView(dash) {
  const etaData = currentBuildingsEta(dash);
  const tbody = el("eta-table-tbody");
  if (!tbody || !etaData) return;

  const p = etaData.planet || {};
  const econ = etaData.planet_economy || {};
  const bldDiv = finiteNum(p.building_time_divisor, 1);
  el("bld-planet-meta").textContent =
    `[${p.coords || "--"}] · Dev L${finiteNum(p.dev_center_level, 0)} · Lab L${finiteNum(p.research_lab_level, 0)} · Micro L${finiteNum(p.microsystem_level, 0)} · Div x${bldDiv}`;

  const econBfPct = finiteNum(econ.blast_furnace_pct, null);
  const bfPct =
    econBfPct !== null ? clampPct(Math.round(econBfPct)) : getSelectedBlastFurnacePct(dash);
  const btnPause = el("btn-b2-pause");
  const btnResume = el("btn-b2-resume");
  if (btnPause && btnResume) {
    btnPause.disabled = bfPct === 0;
    btnResume.disabled = bfPct === 100;
  }

  document.querySelectorAll("[data-bld-cat]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.bldCat === state.bldCategory);
  });

  const isResearch = state.bldCategory === "research";
  let list = isResearch ? etaData.researches || [] : etaData.buildings || [];
  if (!isResearch && state.bldCategory !== "all") {
    list = list.filter((b) => b.category === state.bldCategory);
  }

  if (list.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.className = "empty-cell";
    td.textContent = "No items in selected category.";
    tr.appendChild(td);
    tbody.replaceChildren(tr);
  } else {
    tbody.replaceChildren(
      ...list.map((row) => {
        const tr = document.createElement("tr");
        tr.className = "clickable-row";
        if (row.key === state.selectedUnitKey) tr.classList.add("row-selected");
        tr.addEventListener("click", () => {
          state.selectedUnitKey = row.key;
          savePersistedUiState();
          renderBuildingsView(state.dashboard);
        });

        const tdUnit = document.createElement("td");
        const title = document.createElement("div");
        title.className = "action-title";
        title.textContent = row.name || "Unit";
        if (row.key != null && String(row.key).trim() !== "") {
          const sub = document.createElement("span");
          sub.className = "action-sub tabular";
          sub.textContent = ` (${row.key})`;
          title.appendChild(sub);
        }
        if (row.queued_count > 0 || row.active) {
          const qBadge = document.createElement("span");
          qBadge.className = "rate-chip";
          qBadge.textContent = row.active ? "ACTIVE" : `Q x${row.queued_count}`;
          title.appendChild(qBadge);
        }
        const curLvl = finiteNum(row.current_level, 0);
        const rowPct = finiteNum(row.economy_rate_pct, null);
        if (rowPct !== null && curLvl > 0) {
          const clampedRowPct = clampPct(Math.round(rowPct));
          const rChip = document.createElement("span");
          rChip.className = `rate-chip tabular${clampedRowPct < 100 ? " is-paused" : ""}`;
          rChip.textContent = `${clampedRowPct}%`;
          title.appendChild(rChip);
        }
        tdUnit.appendChild(title);

        const tdLvl = document.createElement("td");
        tdLvl.className = "col-right tabular";
        tdLvl.textContent = String(curLvl);

        const tdNext = document.createElement("td");
        tdNext.className = "col-right tabular";
        tdNext.textContent = `L${row.next_level ?? row.target_level ?? "—"}`;

        const tdMiss = document.createElement("td");
        tdMiss.className = "tabular";
        const missTotal = finiteNum(row.missing_total, 0);
        if (missTotal === 0) {
          const ok = document.createElement("span");
          ok.className = "status-word";
          ok.textContent = "Affordable";
          tdMiss.appendChild(ok);
        } else {
          tdMiss.textContent = formatResourcesInline(row.missing);
        }

        const tdBuildT = document.createElement("td");
        tdBuildT.className = "col-right tabular";
        tdBuildT.textContent = row.build_time_text ?? "—";

        const waitEtaText = row.wait_eta ?? "—";
        const tdReady = document.createElement("td");
        tdReady.className = "col-right tabular";
        const bnTag =
          typeof row.bottleneck === "string" && row.bottleneck.trim()
            ? bottleneckTag(row.bottleneck)
            : "";
        tdReady.textContent = bnTag ? `${waitEtaText} (${bnTag})` : waitEtaText;

        const reachEtaText = row.reach_eta ?? "—";
        const rowWaitH = finiteNum(row.wait_hours, 0);
        const tdReach = document.createElement("td");
        tdReach.className = "col-right tabular";
        const rSpan = document.createElement("span");
        rSpan.className = `eta-pill${reachEtaText === "—" ? " is-blocked" : rowWaitH > 0 ? " is-wait" : ""}`;
        rSpan.textContent = reachEtaText;
        tdReach.appendChild(rSpan);

        const tdCtl = document.createElement("td");
        tdCtl.className = "col-right";
        const ctlWrap = document.createElement("div");
        ctlWrap.className = "row-actions-wrap";
        const canExec = isResearch ? row.can_research_live : row.can_build_live;
        if (canExec) {
          const bBtn = document.createElement("button");
          bBtn.type = "button";
          bBtn.className = "btn btn-primary btn-sm";
          bBtn.textContent = "Build";
          bBtn.addEventListener("click", async (ev) => {
            ev.stopPropagation();
            const cmd = isResearch
              ? { command: "research", research_id: row.research_id, planet_id: p.planet_id, dry_run: false }
              : { command: "build", building_id: row.building_id, planet_id: p.planet_id, dry_run: false };
            try {
              const res = await apiCommand(cmd);
              showFeedback(res.message || "Queued.", "ok");
              await loadDashboard({ refresh: false, force: true });
            } catch (err) {
              showFeedback(`Failed: ${err.message}`, feedbackKindForError(err));
            }
          });
          ctlWrap.appendChild(bBtn);
        } else if (row.unmet_prerequisites && row.unmet_prerequisites.length > 0) {
          const lock = document.createElement("span");
          lock.className = "action-sub";
          lock.textContent = "Locked";
          ctlWrap.appendChild(lock);
        }

        const unitId = isResearch ? row.research_id : row.building_id;
        const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
        if (unitId != null && nextLvl !== null && nextLvl > 0) {
          const qBtn = document.createElement("button");
          qBtn.type = "button";
          qBtn.className = "btn btn-secondary btn-sm";
          qBtn.textContent = "+ Queue";
          qBtn.title = `Add ${row.name} L${nextLvl} to P1 priority queue`;
          qBtn.setAttribute("aria-label", `Add ${row.name} L${nextLvl} to P1 priority queue`);
          qBtn.addEventListener("click", (ev) => {
            ev.stopPropagation();
            enqueueManualCommand({
              kind: isResearch ? "RESEARCH" : "BUILD",
              priority: "P1",
              position: "top",
              planet_id: p.planet_id || 0,
              target_id: unitId,
              target_name: row.name || "",
              target_level: nextLvl,
              deduplicate_pending: true,
            });
          });
          ctlWrap.appendChild(qBtn);
        }
        tdCtl.appendChild(ctlWrap);

        tr.append(tdUnit, tdLvl, tdNext, tdMiss, tdBuildT, tdReady, tdReach, tdCtl);
        return tr;
      })
    );
  }

  renderBuildingInspector(etaData);
}

function renderCombatView(dash) {
  const cmb = currentCombat(dash);
  if (!cmb) return;

  const az = cmb.attack_zone || {};
  const techs = cmb.combat_techs || {};
  el("cmb-az-val").textContent = `${az.label || "AZ —"} (${cmb.newbie_protected ? "Newbie Shield" : "Open"})`;
  el("cmb-az-rule").textContent = `${az.range_text || "—"} pts · Margin ${cmb.safety_margin || 1.4}x · Bash 3/24h`;
  el("cmb-tech-val").textContent = `L${techs.weapons ?? 0} / L${techs.shield ?? 0} / L${techs.defense ?? 0}`;
  el("cmb-tech-sub").textContent = `Spy L${techs.spy ?? 0} · Targeting L${techs.targeting ?? 0} · Scanner L${cmb.galaxy_scanner_level ?? 0}`;
  const fltPow = finiteNum(cmb.stationed_fleet_power, 0);
  const defPow = finiteNum(cmb.stationed_defense_power, 0);
  const cargoCap = finiteNum(cmb.total_cargo_capacity, 0);
  el("cmb-power-val").textContent = `${fmtInt(Math.round(fltPow))} / ${fmtInt(Math.round(defPow))}`;
  el("cmb-cargo-val").textContent = `Cargo ${fmtInt(cargoCap)} · Weapons Fac L${cmb.weapons_factory_level ?? 0}`;

  const shipsBody = el("cmb-ships-tbody");
  const ships = cmb.ships || [];
  const offersById = new Map((cmb.ship_offers || []).map((o) => [o.unit_id, o]));

  if (!state.simPopulatedOnce && ships.some((s) => Number(s.owned) > 0)) {
    populateSimWithOwnedFleet(cmb);
    state.simPopulatedOnce = true;
  }

  shipsBody.replaceChildren(
    ...ships.map((s) => {
      const tr = document.createElement("tr");
      const offer = offersById.get(s.unit_id);

      const tdN = document.createElement("td");
      tdN.textContent = `${s.name} `;
      const c = document.createElement("span");
      c.className = "action-sub tabular";
      c.textContent = `(${s.key})`;
      tdN.appendChild(c);

      const tdO = document.createElement("td");
      tdO.className = "col-right tabular";
      tdO.textContent = fmtInt(s.owned);

      const tdA = document.createElement("td");
      tdA.className = "col-right tabular";
      tdA.textContent = `${fmtInt(s.attack)} / ${fmtInt(s.shield)}`;

      const tdC = document.createElement("td");
      tdC.className = "col-right tabular";
      tdC.textContent = fmtInt(s.cargo);

      const tdOrd = document.createElement("td");
      tdOrd.className = "col-right";
      const wrap = document.createElement("div");
      wrap.className = "row-actions-wrap";
      const inp = document.createElement("input");
      inp.type = "number";
      inp.min = "1";
      inp.value = "1";
      inp.className = "qty-inline";
      inp.setAttribute("aria-label", `Order quantity for ${s.name}`);

      if (offer && offer.can_build) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-primary btn-sm";
        btn.textContent = "Build";
        btn.addEventListener("click", async () => {
          const qty = Math.max(1, Math.round(finiteNum(inp.value, 1)));
          try {
            const res = await apiCommand({
              command: "build_ship",
              ship_id: s.unit_id,
              quantity: qty,
              planet_id: cmb.planet_id,
              dry_run: false,
            });
            showFeedback(res.message || `Ordered ${qty}x ${s.name}.`, "ok");
            await loadDashboard({ refresh: false, force: true });
          } catch (err) {
            showFeedback(`Ship order failed: ${err.message}`, feedbackKindForError(err));
          }
        });
        wrap.append(inp, btn);
      } else {
        wrap.appendChild(inp);
      }

      const qShipBtn = document.createElement("button");
      qShipBtn.type = "button";
      qShipBtn.className = "btn btn-secondary btn-sm";
      qShipBtn.textContent = "+ Queue";
      qShipBtn.title = `Add ${s.name} order to P1 priority queue`;
      qShipBtn.setAttribute("aria-label", `Add ${s.name} order to P1 priority queue`);
      qShipBtn.addEventListener("click", () => {
        const qty = Math.max(1, Math.round(finiteNum(inp.value, 1)));
        enqueueManualCommand({
          kind: "BUILD_SHIP",
          priority: "P1",
          position: "top",
          planet_id: cmb.planet_id || 0,
          target_id: s.unit_id,
          target_name: s.name || "",
          target_level: qty,
        });
      });
      wrap.appendChild(qShipBtn);
      tdOrd.appendChild(wrap);

      tr.append(tdN, tdO, tdA, tdC, tdOrd);
      return tr;
    })
  );

  const defBody = el("cmb-def-tbody");
  const defs = cmb.defenses || [];
  defBody.replaceChildren(
    ...defs.map((d) => {
      const tr = document.createElement("tr");
      const tdN = document.createElement("td");
      tdN.textContent = `${d.name} (${d.key})`;
      const tdO = document.createElement("td");
      tdO.className = "col-right tabular";
      tdO.textContent = fmtInt(d.owned);
      const tdA = document.createElement("td");
      tdA.className = "col-right tabular";
      tdA.textContent = `${fmtInt(d.attack)} / ${fmtInt(d.shield)}`;
      const tdH = document.createElement("td");
      tdH.className = "col-right tabular";
      tdH.textContent = fmtInt(d.hull);
      tr.append(tdN, tdO, tdA, tdH);
      return tr;
    })
  );

  renderRaidTargetsTable(dash, cmb);

  if (cmb.escape_plan && el("fleetsave-output").textContent.startsWith("Click ")) {
    el("fleetsave-output").textContent = JSON.stringify(cmb.escape_plan, null, 2);
  }
}

function renderRaidTargetsTable(dash, cmb) {
  const tbody = el("cmb-targets-tbody");
  const tabBtn = el("cmb-targets-tab");
  if (!tbody) return;

  const ownCoords = new Set((dash.state?.planets || []).map((p) => p.coords));
  const rawTargets = dash.db_summary?.spy_targets || dash.state?.raid_targets || [];
  const targets = rawTargets.filter((t) => {
    const coords = t.coords || t.target_coords;
    return coords && !ownCoords.has(coords) && t.status_color !== "free";
  });
  if (tabBtn) tabBtn.textContent = `Targets (${targets.length})`;

  if (targets.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "empty-cell";
    td.textContent = 'Click "Scan Galaxy" to populate yellow inactive & Spacia targets.';
    tr.appendChild(td);
    tbody.replaceChildren(tr);
    return;
  }

  tbody.replaceChildren(
    ...targets.map((t) => {
      const tr = document.createElement("tr");
      const coords = t.coords || t.target_coords;
      const resMap = t.resources || t;

      const tdC = document.createElement("td");
      tdC.className = "tabular";
      tdC.textContent = coords;

      const tdOwner = document.createElement("td");
      tdOwner.textContent = [t.planet_name, t.owner].filter(Boolean).join(" · ") || "Unknown";

      const tdType = document.createElement("td");
      const badge = document.createElement("span");
      const color = String(t.status_color || "yellow");
      badge.className = `status-word${color === "yellow" || t.is_spacia ? "" : " is-warn"}`;
      badge.textContent = t.is_spacia ? "SPACIA" : color === "yellow" ? "INACTIVE" : color.toUpperCase();
      tdType.appendChild(badge);

      const totalRes =
        finiteNum(resMap.pig_iron, 0) +
        finiteNum(resMap.metal, 0) +
        finiteNum(resMap.kryptonite, 0) +
        finiteNum(resMap.spice, 0);
      const tdLoot = document.createElement("td");
      tdLoot.className = "col-right tabular";
      tdLoot.textContent = Number.isFinite(totalRes) ? (totalRes > 0 ? fmtInt(totalRes) : "Unspied") : "—";

      const tdOps = document.createElement("td");
      tdOps.className = "col-right";
      const wrap = document.createElement("div");
      wrap.className = "row-actions";

      const simBtn = document.createElement("button");
      simBtn.type = "button";
      simBtn.className = "btn btn-secondary btn-sm";
      simBtn.textContent = "Sim";
      simBtn.addEventListener("click", () => {
        populateSimWithOwnedFleet(cmb);
        const def = t.defense || {};
        const flt = t.fleet || {};
        if (el("sim-def-d1")) el("sim-def-d1").value = String(def.d1 || 0);
        if (el("sim-def-d2")) el("sim-def-d2").value = String(def.d2 || 0);
        if (el("sim-def-d4")) el("sim-def-d4").value = String(def.d4 || 0);
        if (el("sim-def-s4")) el("sim-def-s4").value = String(flt.s4 || 0);
        if (el("sim-loot-total")) el("sim-loot-total").value = String(totalRes);
        runCombatSimulation();
      });

      const atkBtn = document.createElement("button");
      atkBtn.type = "button";
      atkBtn.className = "btn btn-primary btn-sm";
      atkBtn.textContent = "Attack";
      atkBtn.addEventListener("click", async () => {
        try {
          showFeedback(`Sending attack to ${coords}...`, "ok");
          const res = await apiCommand({
            command: "attack",
            target_coords: coords,
            planet_id: cmb.planet_id,
            dry_run: false,
          });
          showFeedback(res.message || `Attack processed for ${coords}.`, res.executed ? "ok" : "warn");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Attack failed: ${err.message}`, feedbackKindForError(err));
        }
      });

      wrap.append(simBtn, atkBtn);
      tdOps.appendChild(wrap);

      tr.append(tdC, tdOwner, tdType, tdLoot, tdOps);
      return tr;
    })
  );
}

function populateSimWithOwnedFleet(cmb) {
  const byKey = new Map((cmb?.ships || []).map((s) => [s.key, Number(s.owned || 0)]));
  for (const key of ["s4", "s6", "s7", "s13", "s1", "s2"]) {
    const inp = el(`sim-atk-${key}`);
    if (inp) inp.value = String(byKey.get(key) || 0);
  }
}

async function runCombatSimulation() {
  const atk = {};
  for (const key of ["s4", "s6", "s7", "s13", "s1", "s2"]) {
    const val = Math.max(0, Math.round(finiteNum(el(`sim-atk-${key}`)?.value, 0)));
    if (val > 0) atk[key] = val;
  }
  const defShips = {};
  const s4 = Math.max(0, Math.round(finiteNum(el("sim-def-s4")?.value, 0)));
  if (s4 > 0) defShips.s4 = s4;

  const defDefs = {};
  for (const dk of ["d1", "d2", "d4"]) {
    const val = Math.max(0, Math.round(finiteNum(el(`sim-def-${dk}`)?.value, 0)));
    if (val > 0) defDefs[dk] = val;
  }
  const totalLoot = Math.max(0, Math.round(finiteNum(el("sim-loot-total")?.value, 0)));
  const split = Math.floor(totalLoot / 4);
  const margin = Math.max(1.0, finiteNum(el("sim-margin")?.value, 1.4));

  try {
    const res = await apiCommand({
      command: "simulate_combat",
      attacker_ships: atk,
      defender_ships: defShips,
      defender_defense: defDefs,
      target_resources: { pig_iron: split, metal: split, kryptonite: split, spice: totalLoot - split * 3 },
      safety_margin: margin,
    });
    const sim = res.simulation || {};
    const badge = el("sim-verdict-badge");
    badge.textContent = sim.safe_to_attack ? "SAFE TO RAID" : "UNSAFE / HOLD";
    badge.className = `run-badge tabular${sim.safe_to_attack ? "" : " is-paused"}`;
    const winRatioNum = finiteNum(sim.win_ratio, null);
    const ratioStr =
      sim.win_ratio === Infinity || sim.win_ratio === "Infinity"
        ? "INF"
        : winRatioNum !== null
          ? winRatioNum.toFixed(2)
          : "0.00";
    el("sim-ratio-text").textContent = `Power Ratio: ${ratioStr}x (Threshold ${finiteNum(sim.safety_margin, margin)}x)`;
    el("sim-atk-pwr").textContent = fmtInt(Math.round(finiteNum(sim.attacker_power, 0)));
    el("sim-def-pwr").textContent = fmtInt(Math.round(finiteNum(sim.defender_power, 0)));
    el("sim-cargo").textContent = fmtInt(finiteNum(sim.cargo_capacity, 0));
    el("sim-plunder").textContent = `${fmtInt(finiteNum(sim.total_plunder, 0))} (${fmtInt(Math.round(finiteNum(sim.loot_spice_equivalent, 0)))} SpEq)`;
    el("sim-notes").textContent = sim.notes || "Simulation complete.";
  } catch (err) {
    showFeedback(`Simulation failed: ${err.message}`, feedbackKindForError(err));
  }
}

function renderConfigForm(cfg) {
  if (!cfg) return;
  if (el("cfg-planner_mode")) {
    el("cfg-planner_mode").value = cfg.planner_mode || "priority";
  }
  el("cfg-playstyle").value = cfg.playstyle || "fleeter";
  for (const field of [
    "auto_throttle_blast_furnace",
    "allow_research",
    "allow_shipyard",
    "allow_multi_planet",
    "merchant_mode",
    "allow_fleetsave",
    "allow_galaxy_scan",
    "allow_raids",
    "allow_pvp_in_az",
    "allow_colonize",
    "allow_transmitter",
    "forbid_planetary_solar",
  ]) {
    const box = el(`cfg-${field}`);
    if (box) box.checked = Boolean(cfg[field]);
  }
  el("cfg-storage_warn_ratio").value = cfg.storage_warn_ratio ?? 0.85;
  el("cfg-combat_safety_margin").value = cfg.combat_safety_margin ?? 1.4;
  el("cfg-fleet_save_fuel_reserve_spice").value = cfg.fleet_save_fuel_reserve_spice ?? 500;
  if (el("cfg-action_batch_interval_seconds")) {
    el("cfg-action_batch_interval_seconds").value = cfg.action_batch_interval_seconds ?? 120;
  }
  if (el("cfg-min_colony_fields")) {
    el("cfg-min_colony_fields").value = cfg.min_colony_fields ?? 300;
  }
  if (el("cfg-max_planets")) {
    el("cfg-max_planets").value = cfg.max_planets ?? 11;
  }
  if (el("cfg-galaxy_sweep_pages")) {
    el("cfg-galaxy_sweep_pages").value = cfg.galaxy_sweep_pages ?? 100;
  }
  if (el("cfg-galaxy_sweep_interval_seconds")) {
    el("cfg-galaxy_sweep_interval_seconds").value = cfg.galaxy_sweep_interval_seconds ?? 270;
  }
  if (el("cfg-max_actions_per_hour")) {
    el("cfg-max_actions_per_hour").value = cfg.max_actions_per_hour ?? 60;
  }
  if (el("cfg-ga_population_size")) {
    el("cfg-ga_population_size").value = cfg.ga_population_size ?? 24;
  }
  if (el("cfg-ga_generations")) {
    el("cfg-ga_generations").value = cfg.ga_generations ?? 12;
  }
  if (el("cfg-ga_horizon_steps")) {
    el("cfg-ga_horizon_steps").value = cfg.ga_horizon_steps ?? 6;
  }
  if (el("cfg-ga_seed")) {
    el("cfg-ga_seed").value = cfg.ga_seed != null ? String(cfg.ga_seed) : "";
  }
}

const AG_BOOL_FIELDS = [
  "auto_throttle_blast_furnace",
  "merchant_mode",
  "allow_research",
  "allow_shipyard",
  "allow_multi_planet",
  "allow_fleetsave",
  "allow_galaxy_scan",
  "allow_raids",
  "allow_pvp_in_az",
  "allow_colonize",
  "allow_transmitter",
  "forbid_planetary_solar",
];

const AG_NUM_DEFAULTS = {
  ga_population_size: 24,
  ga_generations: 12,
  ga_horizon_steps: 6,
  ga_mutation_rate: 0.25,
  ga_nn_weight: 0.35,
  storage_warn_ratio: 0.85,
  blast_pig_iron_max_share: 0.7,
  combat_safety_margin: 1.4,
  raid_min_loot_spice_eq: 1500,
  hot_planet_solar_temp_threshold: 40,
  min_free_fields_warning: 20,
  min_colony_fields: 300,
  max_planets: 11,
  galaxy_sweep_interval_seconds: 270,
  galaxy_sweep_pages: 100,
  fleet_save_fuel_reserve_spice: 500,
  action_batch_interval_seconds: 120,
  max_actions_per_hour: 60,
};

const AG_SEMANTIC_EDGE_TYPES = new Set([
  "priority_gate",
  "prerequisite",
  "level_chain",
  "energy_support",
  "throttle_control",
  "storage_cap",
  "resource_feed",
  "loot_feed",
]);

function syncAgModeSwitchButtons(modeRaw) {
  const norm = String(modeRaw || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
  document.querySelectorAll("[data-ag-mode]").forEach((btn) => {
    const isAct = btn.dataset.agMode === norm;
    btn.classList.toggle("active", isAct);
    btn.setAttribute("aria-checked", isAct ? "true" : "false");
  });
  const gaGroup = el("ag-ga-params-group");
  if (gaGroup) {
    gaGroup.classList.toggle("is-muted", norm !== "ga_nn");
  }
}

function setAgDirtyState(dirty) {
  const isDirty = Boolean(dirty);
  state.agSimDirty = isDirty;
  const pill = el("ag-sim-status-pill");
  if (pill) {
    pill.textContent = isDirty ? "SIMULATION PREVIEW · UNSAVED" : "LIVE ACTIVE CONFIG";
    pill.classList.toggle("is-sim", isDirty);
    pill.classList.toggle("is-live", !isDirty);
  }
  const badge = el("ag-dirty-badge");
  if (badge) {
    badge.textContent = isDirty ? "MODIFIED (PREVIEW)" : "ACTIVE SYNCED";
    badge.classList.toggle("is-dirty", isDirty);
    badge.classList.toggle("is-synced", !isDirty);
  }
  const note = el("ag-sim-note");
  if (note) {
    note.textContent = isDirty
      ? "Preview mode active: active StrategyConfig and config.yaml remain unchanged until applied."
      : "Edits simulate a preview graph without modifying active config.yaml until applied.";
  }
}

function populateAgParamControls(cfg) {
  if (!cfg || typeof cfg !== "object") return;
  const modeVal = String(cfg.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
  const modeSel = el("ag-cfg-planner_mode");
  if (modeSel) {
    modeSel.value = modeVal;
  }
  syncAgModeSwitchButtons(modeVal);

  const playSel = el("ag-cfg-playstyle");
  if (playSel) {
    const playVal = String(cfg.playstyle || "fleeter");
    const known = Array.from(playSel.children || []).some((opt) => opt.value === playVal);
    if (!known && playVal) {
      const dynOpt = document.createElement("option");
      dynOpt.value = playVal;
      dynOpt.textContent = playVal;
      dynOpt.selected = true;
      playSel.appendChild(dynOpt);
    }
    playSel.value = playVal;
  }

  for (const [numField, fallback] of Object.entries(AG_NUM_DEFAULTS)) {
    const inp = el(`ag-cfg-${numField}`);
    if (inp) {
      const v = finiteNum(cfg[numField], fallback);
      inp.value = String(v);
    }
  }

  const seedInp = el("ag-cfg-ga_seed");
  if (seedInp) {
    seedInp.value = cfg.ga_seed != null ? String(cfg.ga_seed) : "";
  }

  for (const boolField of AG_BOOL_FIELDS) {
    const box = el(`ag-cfg-${boolField}`);
    if (box) {
      box.checked = Boolean(cfg[boolField]);
    }
  }
}

function collectAgDraftPayload() {
  const modeSel = el("ag-cfg-planner_mode");
  const playSel = el("ag-cfg-playstyle");
  const payload = {
    planner_mode: modeSel ? String(modeSel.value || "priority") : "priority",
    playstyle: playSel ? String(playSel.value || "fleeter") : "fleeter",
  };

  for (const boolField of AG_BOOL_FIELDS) {
    const box = el(`ag-cfg-${boolField}`);
    if (box) {
      payload[boolField] = Boolean(box.checked);
    }
  }

  for (const numField of Object.keys(AG_NUM_DEFAULTS)) {
    const inp = el(`ag-cfg-${numField}`);
    if (!inp) continue;
    const raw = String(inp.value ?? "").trim();
    if (raw === "") {
      payload[numField] = "";
    } else if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(raw)) {
      const parsed = Number(raw);
      payload[numField] = Number.isFinite(parsed) ? parsed : raw;
    } else {
      payload[numField] = raw;
    }
  }

  const seedInp = el("ag-cfg-ga_seed");
  if (seedInp) {
    const rawSeed = String(seedInp.value ?? "").trim();
    if (rawSeed === "") {
      payload.ga_seed = null;
    } else if (/^-?\d+$/.test(rawSeed)) {
      const parsedSeed = Number(rawSeed);
      payload.ga_seed = Number.isSafeInteger(parsedSeed) ? parsedSeed : rawSeed;
    } else {
      payload.ga_seed = rawSeed;
    }
  }

  return payload;
}

function buildClientFallbackGraph(dash) {
  const cfg = dash?.config || {};
  const mode = String(cfg.planner_mode || dash?.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
  const planList = Array.isArray(dash?.plan) ? dash.plan : [];
  const planets = Array.isArray(dash?.state?.planets) ? dash.state.planets : [];
  const planetById = new Map();
  planets.forEach((p) => {
    const pid = finiteNum(p?.planet_id, 0);
    if (pid > 0) planetById.set(pid, p);
  });

  const laneCounts = new Map([["lane:global", 0]]);
  planets.forEach((p) => {
    const pid = finiteNum(p?.planet_id, 0);
    if (pid > 0) laneCounts.set(`lane:planet:${pid}`, 0);
  });

  let execRank = 0;
  const nodes = planList.map((act, idx) => {
    const kind = String(act?.kind || "BUILD").toUpperCase();
    const pid = Math.max(0, Math.round(finiteNum(act?.planet_id, 0)));
    const pObj = planetById.get(pid) || null;
    const laneId = kind === "RESEARCH" || kind === "CLAIM_TUTORIAL" || pid <= 0 ? "lane:global" : `lane:planet:${pid}`;
    laneCounts.set(laneId, (laneCounts.get(laneId) || 0) + 1);
    const execNow = Boolean(act?.executable_now);
    const missing = {
      pig_iron: Math.max(0, Math.round(finiteNum(act?.missing?.pig_iron, 0))),
      metal: Math.max(0, Math.round(finiteNum(act?.missing?.metal, 0))),
      kryptonite: Math.max(0, Math.round(finiteNum(act?.missing?.kryptonite, 0))),
      spice: Math.max(0, Math.round(finiteNum(act?.missing?.spice, 0))),
    };
    const hasMissing = missing.pig_iron > 0 || missing.metal > 0 || missing.kryptonite > 0 || missing.spice > 0;
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
      target_id: finiteNum(act?.target_id, null),
      target_name: String(act?.target_name || kind),
      target_level: finiteNum(act?.target_level, null),
      reason,
      executable_now: execNow,
      cost: {
        pig_iron: Math.max(0, Math.round(finiteNum(act?.cost?.pig_iron, 0))),
        metal: Math.max(0, Math.round(finiteNum(act?.cost?.metal, 0))),
        kryptonite: Math.max(0, Math.round(finiteNum(act?.cost?.kryptonite, 0))),
        spice: Math.max(0, Math.round(finiteNum(act?.cost?.spice, 0))),
      },
      missing,
      wait_seconds: finiteNum(act?.wait_seconds, 0),
      wait_eta: String(act?.wait_eta || "Ready"),
      duration_seconds: Math.max(0, finiteNum(act?.duration_seconds, 0)),
      duration_text: String(act?.duration_text || "Instant"),
      total_seconds: finiteNum(act?.total_seconds, 0),
      total_eta: String(act?.total_eta || "Ready"),
      eta_bottleneck: act?.eta_bottleneck ? String(act.eta_bottleneck) : null,
      ga_step: null,
      in_degree: idx > 0 ? 1 : 0,
      out_degree: idx + 1 < planList.length ? 1 : 0,
    };
  });

  const edges = [];
  for (let i = 0; i + 1 < nodes.length; i += 1) {
    edges.push({
      id: `edge_act_${i}_act_${i + 1}`,
      source: `act_${i}`,
      target: `act_${i + 1}`,
      primary_type: nodes[i].lane_id === nodes[i + 1].lane_id ? "queue_order" : "execution_order",
      types: [nodes[i].lane_id === nodes[i + 1].lane_id ? "queue_order" : "execution_order"],
      label: `Planned step #${i + 1} -> #${i + 2}`,
    });
  }

  const lanes = [];
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
      const pid = Math.max(0, Math.round(finiteNum(p?.planet_id, 0)));
      if (pid <= 0) return;
      const lid = `lane:planet:${pid}`;
      lanes.push({
        id: lid,
        scope: "planet",
        label: p?.coords ? `${p.name || `Colony ${pid}`} [${p.coords}]` : String(p?.name || `Colony ${pid}`),
        planet_id: pid,
        coords: String(p?.coords || ""),
        planet_type: String(p?.planet_type || "normal"),
        energy: Math.round(finiteNum(p?.energy?.produced, 0) - finiteNum(p?.energy?.used, 0)),
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
      critical_path_eta: nodes.length > 0 ? String(nodes[nodes.length - 1].total_eta || "Ready") : "Ready",
      primary_bottleneck: null,
      by_priority: { P0: 0, P1: 0, P2: nodes.length, P3: 0 },
      by_edge_type: {},
    },
    ga_summary: dash?.optimizer_summary || null,
  };
}

function edgeVisualSpec(edge) {
  const pType = String(edge?.primary_type || "execution_order");
  if (pType === "priority_gate") {
    return { cls: "is-gate", marker: "ag-arrow-gate" };
  }
  if (pType === "prerequisite" || pType === "level_chain") {
    return { cls: "is-prereq", marker: "ag-arrow-prereq" };
  }
  if (
    pType === "energy_support" ||
    pType === "throttle_control" ||
    pType === "storage_cap" ||
    pType === "resource_feed" ||
    pType === "loot_feed"
  ) {
    return { cls: "is-resource", marker: "ag-arrow-resource" };
  }
  if (pType === "queue_order") {
    return { cls: "is-queue", marker: "ag-arrow-queue" };
  }
  return { cls: "is-seq", marker: "ag-arrow-seq" };
}

function edgeMatchesFilter(edge, filterMode) {
  if (filterMode !== "deps") return true;
  const types = Array.isArray(edge?.types) ? edge.types : [edge?.primary_type];
  return types.some((t) => AG_SEMANTIC_EDGE_TYPES.has(String(t || "")));
}

function formatNodeTargetLabel(node) {
  const base = String(node?.target_name || node?.kind || "Action");
  const lvl = finiteNum(node?.target_level, null);
  const kind = String(node?.kind || "");
  if (lvl === null) return base;
  if (kind === "THROTTLE_BUILDING") {
    return `${base} → ${Math.round(lvl)}%`;
  }
  if (kind === "BUILD_SHIP") {
    if (/\(x\d+\)$/i.test(base)) return base;
    return `${base} (x${Math.max(1, Math.round(lvl))})`;
  }
  if (kind === "BUILD" || kind === "WAIT_RESOURCES" || kind === "RESEARCH") {
    return `${base} L${Math.round(lvl)}`;
  }
  if (kind === "DEMOLISH_BUILDING") {
    return `Demolish ${base} → L${Math.round(lvl)}`;
  }
  return base;
}

function formatNodeStatusShort(status) {
  if (status === "ready") return "READY";
  if (status === "waiting_resources") return "WAIT";
  if (status === "blocked_gate") return "HOLD";
  return "QUEUE";
}

function sanitizeMainWindowText(rawText) {
  // Never emit substring "generation" inside #ag-main-window so findGaNnHudPanel() stays pinned to #gann-calc-hud
  return String(rawText ?? "").replace(/generations?/gi, "cycles");
}

function renderAgNodeInspector(graph, selectedNode) {
  const stepEl = el("ag-insp-step");
  const titleEl = el("ag-insp-title");
  const metaEl = el("ag-insp-meta");
  const depsEl = el("ag-insp-deps");
  if (!stepEl || !titleEl || !metaEl || !depsEl) return;

  if (!selectedNode) {
    stepEl.textContent = "#—";
    titleEl.textContent = "Select a node in the graph to inspect dependencies and ETAs";
    metaEl.textContent = "Click any node card above to highlight upstream prerequisites and downstream dependents.";
    depsEl.replaceChildren();
    return;
  }

  const orderNum = Math.max(1, Math.round(finiteNum(selectedNode.order_index, 0)) + 1);
  stepEl.textContent = `#${orderNum}`;
  const coordsTxt = selectedNode.planet_coords ? `[${selectedNode.planet_coords}]` : "[GLOBAL]";
  titleEl.textContent = sanitizeMainWindowText(
    `${selectedNode.priority || "P2"} · ${formatNodeTargetLabel(selectedNode)} ${coordsTxt}`
  );

  const waitTxt = String(selectedNode.wait_eta || "Ready");
  const durTxt = String(selectedNode.duration_text || "Instant");
  const totTxt = String(selectedNode.total_eta || "Ready");
  const gaStep = selectedNode.ga_step;
  const gaTiming =
    gaStep && typeof gaStep === "object"
      ? ` · GA Step #${Math.round(finiteNum(gaStep.step_index, 0)) + 1} (${Math.round(finiteNum(gaStep.start_t, 0))}s → ${Math.round(finiteNum(gaStep.finish_t, 0))}s)`
      : "";
  const reasonClean = sanitizeMainWindowText(selectedNode.reason || "Planned empire operation.");
  metaEl.textContent = `Wait: ${waitTxt} · Duration: ${durTxt} · Total: ${totTxt}${gaTiming} — ${reasonClean}`;

  const nodesById = new Map((graph?.nodes || []).map((n) => [n.id, n]));
  const allEdges = Array.isArray(graph?.edges) ? graph.edges : [];
  const incoming = allEdges.filter((e) => e.target === selectedNode.id);
  const outgoing = allEdges.filter((e) => e.source === selectedNode.id);

  const pills = [];
  if (incoming.length === 0 && outgoing.length === 0) {
    const noneSpan = document.createElement("span");
    noneSpan.className = "ag-dep-pill";
    noneSpan.textContent = "Independent root action (0 links)";
    pills.push(noneSpan);
  } else {
    incoming.slice(0, 4).forEach((edge) => {
      const src = nodesById.get(edge.source);
      const pill = document.createElement("span");
      pill.className = "ag-dep-pill is-up";
      const srcNum = src ? Math.round(finiteNum(src.order_index, 0)) + 1 : "?";
      pill.textContent = sanitizeMainWindowText(
        `← #${srcNum} ${src ? src.target_name : edge.source} (${edge.primary_type})`
      );
      pill.title = sanitizeMainWindowText(edge.label || "");
      pills.push(pill);
    });
    outgoing.slice(0, 4).forEach((edge) => {
      const dst = nodesById.get(edge.target);
      const pill = document.createElement("span");
      pill.className = "ag-dep-pill is-down";
      const dstNum = dst ? Math.round(finiteNum(dst.order_index, 0)) + 1 : "?";
      pill.textContent = sanitizeMainWindowText(
        `→ #${dstNum} ${dst ? dst.target_name : edge.target} (${edge.primary_type})`
      );
      pill.title = sanitizeMainWindowText(edge.label || "");
      pills.push(pill);
    });
  }
  depsEl.replaceChildren(...pills);
}

function updateAgSelectionHighlight(graph) {
  const selId = state.agSelectedNodeId;
  const allEdges = Array.isArray(graph?.edges) ? graph.edges : [];
  const upstreamIds = new Set();
  const downstreamIds = new Set();
  if (selId) {
    allEdges.forEach((e) => {
      if (e.target === selId) upstreamIds.add(e.source);
      if (e.source === selId) downstreamIds.add(e.target);
    });
  }

  document.querySelectorAll(".ag-node-card").forEach((card) => {
    const nid = card.getAttribute("data-node-id") || card.dataset.nodeId;
    const isSel = Boolean(selId && nid === selId);
    const isUp = Boolean(selId && upstreamIds.has(nid));
    const isDown = Boolean(selId && downstreamIds.has(nid));
    card.classList.toggle("is-selected", isSel);
    card.classList.toggle("is-upstream", !isSel && isUp);
    card.classList.toggle("is-downstream", !isSel && isDown);
    card.classList.toggle("is-dimmed", Boolean(selId && !isSel && !isUp && !isDown));
    card.setAttribute("aria-pressed", isSel ? "true" : "false");
  });

  document.querySelectorAll(".ag-edge-path").forEach((pathEl) => {
    const src = pathEl.getAttribute("data-source");
    const dst = pathEl.getAttribute("data-target");
    const connected = Boolean(selId && (src === selId || dst === selId));
    pathEl.classList.toggle("is-highlighted", connected);
    pathEl.classList.toggle("is-dimmed", Boolean(selId && !connected));
  });

  const selectedObj = selId ? (graph?.nodes || []).find((n) => n.id === selId) || null : null;
  renderAgNodeInspector(graph, selectedObj);
}

function renderActionGraphView(graphInput) {
  const graph = graphInput || (state.dashboard ? buildClientFallbackGraph(state.dashboard) : null);
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const allEdges = Array.isArray(graph?.edges) ? graph.edges : [];
  const visibleEdges = allEdges.filter((e) => edgeMatchesFilter(e, state.agEdgeFilter));
  const summary = graph?.summary || {};
  const mode = String(graph?.mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";

  syncAgModeSwitchButtons(mode);
  if (!state.agSimDirty) {
    setAgDirtyState(false);
  }

  // Update 4-cell KPI telemetry strip (strictly avoiding substring "generation")
  const totalNodes = Math.max(0, Math.round(finiteNum(summary.total_nodes, nodes.length)));
  const readyCount = Math.max(
    0,
    Math.round(finiteNum(summary.ready_count, nodes.filter((n) => n.status === "ready").length))
  );
  const waitingCount = Math.max(
    0,
    Math.round(
      finiteNum(summary.waiting_count, nodes.filter((n) => n.status === "waiting_resources").length)
    )
  );
  const blockedCount = Math.max(
    0,
    Math.round(finiteNum(summary.blocked_count, nodes.filter((n) => n.status === "blocked_gate").length))
  );
  if (el("ag-kpi-nodes")) {
    el("ag-kpi-nodes").textContent = `${totalNodes} ${totalNodes === 1 ? "node" : "nodes"}`;
  }
  if (el("ag-kpi-ready")) {
    el("ag-kpi-ready").textContent =
      blockedCount > 0
        ? `${readyCount} ready · ${waitingCount} wait · ${blockedCount} hold`
        : `${readyCount} ready · ${waitingCount} waiting`;
  }

  const byEdgeType = summary.by_edge_type || {};
  const prereqCount =
    Math.round(finiteNum(byEdgeType.prerequisite, 0)) +
    Math.round(finiteNum(byEdgeType.level_chain, 0)) +
    Math.round(finiteNum(byEdgeType.priority_gate, 0));
  const resCount =
    Math.round(finiteNum(byEdgeType.energy_support, 0)) +
    Math.round(finiteNum(byEdgeType.throttle_control, 0)) +
    Math.round(finiteNum(byEdgeType.storage_cap, 0)) +
    Math.round(finiteNum(byEdgeType.resource_feed, 0)) +
    Math.round(finiteNum(byEdgeType.loot_feed, 0));
  if (el("ag-kpi-edges")) {
    el("ag-kpi-edges").textContent = `${visibleEdges.length} ${visibleEdges.length === 1 ? "edge" : "edges"}`;
  }
  if (el("ag-kpi-dep-breakdown")) {
    el("ag-kpi-dep-breakdown").textContent = `${prereqCount} prereq · ${resCount} resource`;
  }

  if (el("ag-kpi-horizon")) {
    el("ag-kpi-horizon").textContent = sanitizeMainWindowText(summary.critical_path_eta || "Ready");
  }
  if (el("ag-kpi-bottleneck")) {
    const bneck = summary.primary_bottleneck;
    el("ag-kpi-bottleneck").textContent = bneck
      ? `Bottleneck: ${String(bneck).replace(/_/g, " ")}`
      : "No resource bottleneck";
  }

  const gaSum = graph?.ga_summary;
  if (el("ag-kpi-engine")) {
    if (mode === "ga_nn" && gaSum && typeof gaSum === "object") {
      const impTxt = fmtImprovementPct(
        gaSum.improvement_pct,
        finiteNum(gaSum.baseline_fitness ?? gaSum.initial_best_fitness, null),
        finiteNum(gaSum.best_fitness ?? gaSum.final_best_fitness, null)
      );
      el("ag-kpi-engine").textContent = sanitizeMainWindowText(`GA+NN · ${impTxt}`);
    } else {
      el("ag-kpi-engine").textContent = mode === "ga_nn" ? "GA+NN · EVOLUTIONARY" : "PRIORITY · P0–P3";
    }
  }
  if (el("ag-kpi-engine-sub")) {
    if (mode === "ga_nn" && gaSum && typeof gaSum === "object") {
      const pop = Math.max(1, Math.round(finiteNum(gaSum.population_size, 24)));
      const cyc = Math.max(1, Math.round(finiteNum(gaSum.generations, 12)));
      const ms = Math.max(0, Math.round(finiteNum(gaSum.elapsed_ms, 0)));
      el("ag-kpi-engine-sub").textContent = `Pop ${pop} · ${cyc} cycles · ${ms}ms`;
    } else {
      const effPlay = String(graph?.effective_config?.playstyle || "fleeter");
      const isPreview = Boolean(graph?.is_dirty ?? graph?.simulated);
      el("ag-kpi-engine-sub").textContent = sanitizeMainWindowText(
        `${isPreview ? "Simulated" : "Active"} · ${effPlay}`
      );
    }
  }

  const stageEl = el("ag-canvas-stage");
  const lanesLayer = el("ag-lanes-layer");
  const edgesLayer = el("ag-edges-layer");
  const nodesLayer = el("ag-nodes-layer");
  const emptyEl = el("ag-empty-state");
  if (!stageEl || !lanesLayer || !edgesLayer || !nodesLayer || !emptyEl) return;

  if (nodes.length === 0) {
    lanesLayer.replaceChildren();
    edgesLayer.replaceChildren();
    nodesLayer.replaceChildren();
    stageEl.hidden = true;
    emptyEl.hidden = false;
    state.agSelectedNodeId = null;
    renderAgNodeInspector(graph, null);
    return;
  }

  stageEl.hidden = false;
  emptyEl.hidden = true;

  if (state.agSelectedNodeId && !nodes.some((n) => n.id === state.agSelectedNodeId)) {
    state.agSelectedNodeId = null;
  }

  // Build ordered active swimlanes
  const rawLanes = Array.isArray(graph?.lanes) ? graph.lanes : [];
  const activeLanes = [];
  const laneIdxById = new Map();
  rawLanes.forEach((ln) => {
    const lid = String(ln?.id || "");
    if (!lid) return;
    const hasNodes = nodes.some((n) => n.lane_id === lid);
    if (hasNodes) {
      laneIdxById.set(lid, activeLanes.length);
      activeLanes.push(ln);
    }
  });
  nodes.forEach((n) => {
    const lid = String(n.lane_id || "lane:global");
    if (!laneIdxById.has(lid)) {
      laneIdxById.set(lid, activeLanes.length);
      activeLanes.push({
        id: lid,
        scope: lid === "lane:global" ? "global" : "planet",
        label:
          lid === "lane:global"
            ? "Empire & Global Research"
            : n.planet_coords
              ? `${n.planet_name || "Colony"} [${n.planet_coords}]`
              : String(n.planet_name || lid),
        planet_id: finiteNum(n.planet_id, 0),
        coords: String(n.planet_coords || ""),
        energy: 0,
        node_count: 1,
      });
    }
  });

  // Deterministic coordinate layout (zero DOM geometry calls)
  const LANE_HEADER_W = 168;
  const PAD_X = 20;
  const PAD_Y = 14;
  const CARD_W = 228;
  const CARD_H = 92;
  const COL_GAP = 68;
  const STACK_GAP = 14;
  const LANE_PAD_Y = 16;

  const cellStacks = new Map();
  const maxStackByLane = new Array(activeLanes.length).fill(1);
  let maxCol = 0;

  const nodePlacement = new Map();
  nodes.forEach((n) => {
    const lIdx = laneIdxById.get(String(n.lane_id || "lane:global")) ?? 0;
    const colIdx = Math.max(0, Math.round(finiteNum(n.topological_layer, 0)));
    if (colIdx > maxCol) maxCol = colIdx;
    const cellKey = `${lIdx}:${colIdx}`;
    const stackIdx = cellStacks.get(cellKey) || 0;
    cellStacks.set(cellKey, stackIdx + 1);
    if (stackIdx + 1 > maxStackByLane[lIdx]) {
      maxStackByLane[lIdx] = stackIdx + 1;
    }
    nodePlacement.set(n.id, { laneIdx: lIdx, colIdx, stackIdx });
  });

  const laneTops = [];
  const laneHeights = [];
  let cursorY = PAD_Y;
  for (let i = 0; i < activeLanes.length; i += 1) {
    const stacks = maxStackByLane[i];
    const h = stacks * CARD_H + Math.max(0, stacks - 1) * STACK_GAP + LANE_PAD_Y * 2;
    laneTops.push(cursorY);
    laneHeights.push(h);
    cursorY += h;
  }

  const totalWidth = Math.max(
    680,
    LANE_HEADER_W + PAD_X * 2 + (maxCol + 1) * CARD_W + maxCol * COL_GAP + 24
  );
  const totalHeight = Math.max(240, cursorY + PAD_Y);

  stageEl.style.width = `${totalWidth}px`;
  stageEl.style.height = `${totalHeight}px`;
  lanesLayer.style.width = `${totalWidth}px`;
  lanesLayer.style.height = `${totalHeight}px`;
  edgesLayer.style.width = `${totalWidth}px`;
  edgesLayer.style.height = `${totalHeight}px`;
  nodesLayer.style.width = `${totalWidth}px`;
  nodesLayer.style.height = `${totalHeight}px`;

  // 1. Render swimlane bands
  const laneEls = activeLanes.map((ln, idx) => {
    const band = document.createElement("div");
    band.className = idx % 2 === 1 ? "ag-lane-band is-alt" : "ag-lane-band";
    band.setAttribute("data-lane-id", String(ln.id || ""));
    band.style.top = `${laneTops[idx]}px`;
    band.style.width = `${totalWidth}px`;
    band.style.height = `${laneHeights[idx]}px`;

    const hdr = document.createElement("div");
    hdr.className = "ag-lane-header";
    const titleSpan = document.createElement("span");
    titleSpan.className = "ag-lane-title";
    titleSpan.textContent = sanitizeMainWindowText(ln.label || ln.id || "Lane");
    const metaSpan = document.createElement("span");
    metaSpan.className = "ag-lane-meta tabular";
    const laneNodeCnt = nodes.filter((n) => n.lane_id === ln.id).length;
    if (ln.scope === "global") {
      metaSpan.textContent = `${laneNodeCnt} global ${laneNodeCnt === 1 ? "task" : "tasks"}`;
    } else {
      const eNet = Math.round(finiteNum(ln.energy, 0));
      const eSign = eNet > 0 ? `+${eNet}` : String(eNet);
      metaSpan.textContent = `${laneNodeCnt} ${laneNodeCnt === 1 ? "task" : "tasks"} · Energy ${eSign}`;
    }
    hdr.append(titleSpan, metaSpan);
    band.appendChild(hdr);
    return band;
  });
  lanesLayer.replaceChildren(...laneEls);

  // 2. Compute exact node pixel coordinates & render <button class="ag-node-card">
  const coordsById = new Map();
  const cardEls = nodes.map((node) => {
    const plc = nodePlacement.get(node.id) || { laneIdx: 0, colIdx: 0, stackIdx: 0 };
    const x = LANE_HEADER_W + PAD_X + plc.colIdx * (CARD_W + COL_GAP);
    const y = laneTops[plc.laneIdx] + LANE_PAD_Y + plc.stackIdx * (CARD_H + STACK_GAP);
    coordsById.set(node.id, { x, y, colIdx: plc.colIdx, laneIdx: plc.laneIdx });

    const status = String(node.status || "queued");
    const statusCls =
      status === "ready"
        ? "is-ready"
        : status === "waiting_resources"
          ? "is-waiting"
          : status === "blocked_gate"
            ? "is-blocked"
            : "is-queued";
    const prio = String(node.priority || "P2").toUpperCase();
    const prioCls =
      prio === "P0" ? "is-p0" : prio === "P1" ? "is-p1" : prio === "P2" ? "is-p2" : "is-p3";

    const card = document.createElement("button");
    card.type = "button";
    card.className = `ag-node-card ${statusCls}`;
    card.setAttribute("role", "listitem");
    card.setAttribute("data-node-id", String(node.id));
    card.setAttribute("data-node-kind", String(node.kind || ""));
    card.setAttribute("data-node-priority", prio);
    card.setAttribute("data-node-status", status);
    card.setAttribute("aria-pressed", state.agSelectedNodeId === node.id ? "true" : "false");
    card.style.left = `${x}px`;
    card.style.top = `${y}px`;
    card.title = sanitizeMainWindowText(node.reason || "");

    const head = document.createElement("div");
    head.className = "ag-node-head";
    const badges = document.createElement("div");
    badges.className = "ag-node-badges";

    const stepTag = document.createElement("span");
    stepTag.className = "ag-step-tag tabular";
    const ord = Math.max(1, Math.round(finiteNum(node.order_index, 0)) + 1);
    const gaStepIdx = finiteNum(node?.ga_step?.step_index, null);
    stepTag.textContent = gaStepIdx !== null ? `#${ord} · G${Math.round(gaStepIdx) + 1}` : `#${ord}`;

    const prioTag = document.createElement("span");
    prioTag.className = `ag-prio-tag tabular ${prioCls}`;
    prioTag.textContent = prio;

    badges.append(stepTag, prioTag);

    const stateTag = document.createElement("span");
    stateTag.className = `ag-state-tag tabular ${statusCls}`;
    stateTag.textContent = formatNodeStatusShort(status);

    head.append(badges, stateTag);

    const title = document.createElement("div");
    title.className = "ag-node-title";
    title.textContent = sanitizeMainWindowText(formatNodeTargetLabel(node));

    const foot = document.createElement("div");
    foot.className = "ag-node-foot";
    const coordsSpan = document.createElement("span");
    coordsSpan.className = "ag-node-coords";
    coordsSpan.textContent = node.planet_coords ? `[${node.planet_coords}]` : "[GLOBAL]";
    const etaSpan = document.createElement("span");
    etaSpan.className = "ag-node-eta";
    etaSpan.textContent = sanitizeMainWindowText(`ETA ${node.total_eta || "Ready"}`);
    foot.append(coordsSpan, etaSpan);

    card.append(head, title, foot);
    card.addEventListener("click", () => {
      state.agSelectedNodeId = state.agSelectedNodeId === node.id ? null : node.id;
      updateAgSelectionHighlight(graph);
    });
    return card;
  });
  nodesLayer.replaceChildren(...cardEls);

  // 3. Dynamically create <svg id="ag-edges-svg"> via createElementNS (preserving static single-SVG invariant)
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("id", "ag-edges-svg");
  svg.setAttribute("viewBox", `0 0 ${totalWidth} ${totalHeight}`);
  svg.setAttribute("width", String(totalWidth));
  svg.setAttribute("height", String(totalHeight));

  const defs = document.createElementNS(ns, "defs");
  const markerSpecs = [
    ["ag-arrow-seq", "#32e8ff"],
    ["ag-arrow-prereq", "#16ec98"],
    ["ag-arrow-resource", "#efb03b"],
    ["ag-arrow-queue", "#9eb3b8"],
    ["ag-arrow-gate", "#ff6873"],
  ];
  markerSpecs.forEach(([mId, color]) => {
    const marker = document.createElementNS(ns, "marker");
    marker.setAttribute("id", mId);
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "6");
    marker.setAttribute("markerHeight", "6");
    marker.setAttribute("orient", "auto-start-reverse");
    const poly = document.createElementNS(ns, "path");
    poly.setAttribute("d", "M 0 1.5 L 9 5 L 0 8.5 z");
    poly.setAttribute("fill", color);
    marker.appendChild(poly);
    defs.appendChild(marker);
  });
  svg.appendChild(defs);

  visibleEdges.forEach((edge) => {
    const p1 = coordsById.get(edge.source);
    const p2 = coordsById.get(edge.target);
    if (!p1 || !p2) return;
    const x1 = p1.x + CARD_W;
    const y1 = p1.y + CARD_H / 2;
    const x2 = p2.x;
    const y2 = p2.y + CARD_H / 2;
    const dx = Math.max(36, Math.abs(x2 - x1) * 0.45);
    const sameRowLongSpan = Math.abs(y2 - y1) < 6 && p2.colIdx - p1.colIdx > 1;
    const arcLift = sameRowLongSpan ? -30 : 0;
    const dStr = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${(x1 + dx).toFixed(1)} ${(y1 + arcLift).toFixed(1)}, ${(x2 - dx).toFixed(1)} ${(y2 + arcLift).toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;

    const spec = edgeVisualSpec(edge);
    const pathEl = document.createElementNS(ns, "path");
    pathEl.setAttribute("class", `ag-edge-path ${spec.cls}`);
    pathEl.setAttribute("d", dStr);
    pathEl.setAttribute("marker-end", `url(#${spec.marker})`);
    pathEl.setAttribute("data-edge-id", String(edge.id || ""));
    pathEl.setAttribute("data-source", String(edge.source || ""));
    pathEl.setAttribute("data-target", String(edge.target || ""));
    pathEl.setAttribute("data-edge-type", String(edge.primary_type || ""));
    svg.appendChild(pathEl);
  });

  edgesLayer.replaceChildren(svg);
  updateAgSelectionHighlight(graph);
}

async function refreshActiveActionGraph(forceRefresh = false) {
  const reqSeq = ++state.agSimSeq;
  try {
    const res = await apiCommand({
      command: "action_graph",
      simulated: false,
      refresh: Boolean(forceRefresh),
    });
    if (reqSeq !== state.agSimSeq || state.agSimDirty) return false;
    if (res.action_graph && typeof res.action_graph === "object") {
      state.actionGraphData = res.action_graph;
      if (state.dashboard) {
        state.dashboard = { ...state.dashboard, action_graph: res.action_graph };
      }
      renderActionGraphView(res.action_graph);
      return true;
    }
  } catch (err) {
    if (reqSeq === state.agSimSeq) {
      showFeedback(`Action graph load failed: ${err.message}`, feedbackKindForError(err));
    }
  }
  return false;
}

async function runAgSimulation({ explicit = false } = {}) {
  if (state.agDebounceTimer) {
    clearTimeout(state.agDebounceTimer);
    state.agDebounceTimer = null;
  }
  const draft = collectAgDraftPayload();
  const simMode = String(draft.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
  syncAgModeSwitchButtons(simMode);
  setAgDirtyState(true);

  const reqSeq = ++state.agSimSeq;
  state.agSimRunning += 1;
  const useGaHud = simMode === "ga_nn";
  if (useGaHud) {
    beginGaCalc();
  }
  try {
    const res = await apiCommand({
      command: "action_graph",
      planner_mode: simMode,
      config: draft,
      simulated: true,
      refresh: false,
    });
    if (reqSeq !== state.agSimSeq) return false;
    if (res.action_graph && typeof res.action_graph === "object") {
      const isDirty = Boolean(res.action_graph.is_dirty);
      state.actionGraphData = res.action_graph;
      if (!isDirty && state.dashboard) {
        state.dashboard = { ...state.dashboard, action_graph: res.action_graph };
      }
      setAgDirtyState(isDirty);
      renderActionGraphView(res.action_graph);
      if (explicit) {
        const cnt = Array.isArray(res.action_graph.nodes) ? res.action_graph.nodes.length : 0;
        showFeedback(
          `Simulated ${simMode} action graph (${cnt} planned ${cnt === 1 ? "action" : "actions"} — active config unchanged).`,
          "ok"
        );
      }
      return true;
    }
    return false;
  } catch (err) {
    if (reqSeq === state.agSimSeq) {
      showFeedback(`Simulation preview error: ${err.message}`, feedbackKindForError(err));
    }
    return false;
  } finally {
    state.agSimRunning = Math.max(0, state.agSimRunning - 1);
    if (useGaHud) {
      endGaCalc();
    }
  }
}

function renderRankingChart(samples) {
  const svg = el("rnk-chart");
  if (!svg) return;
  const ns = "http://www.w3.org/2000/svg";
  svg.replaceChildren();

  const w = 420;
  const h = 130;
  const padX = 14;
  const padY = 14;

  for (let i = 1; i <= 3; i += 1) {
    const y = Math.round((h / 4) * i);
    const gridLine = document.createElementNS(ns, "line");
    gridLine.setAttribute("x1", String(padX));
    gridLine.setAttribute("x2", String(w - padX));
    gridLine.setAttribute("y1", String(y));
    gridLine.setAttribute("y2", String(y));
    gridLine.setAttribute("stroke", "#243135");
    gridLine.setAttribute("stroke-dasharray", "3 3");
    svg.appendChild(gridLine);
  }

  const vals = (samples || [])
    .map((s) => finiteNum(s?.estimated_points, null))
    .filter((v) => v !== null);

  if (vals.length === 0) {
    const txt = document.createElementNS(ns, "text");
    txt.setAttribute("x", String(w / 2));
    txt.setAttribute("y", String(h / 2));
    txt.setAttribute("text-anchor", "middle");
    txt.setAttribute("fill", "#9eb3b8");
    txt.setAttribute("font-size", "11");
    txt.textContent = "Collecting samples into SQLite (rank_snapshots)...";
    svg.appendChild(txt);
    return;
  }

  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const rawSpan = maxV - minV;
  const span = Number.isFinite(rawSpan) && rawSpan >= 0.5 ? rawSpan : 0.5;

  const toXY = (idx, val) => {
    const x = vals.length === 1 ? w / 2 : padX + (idx / (vals.length - 1)) * (w - padX * 2);
    const ratio = Number.isFinite(rawSpan) && rawSpan > 0 ? (val - minV) / span : 0;
    const rawY = h - padY - (Number.isFinite(ratio) ? ratio : 0) * (h - padY * 2);
    const y = Number.isFinite(rawY) ? rawY : h / 2;
    return [x.toFixed(1), y.toFixed(1)];
  };

  if (vals.length >= 2) {
    const ptsAttr = vals.map((v, i) => toXY(i, v).join(",")).join(" ");
    const poly = document.createElementNS(ns, "polyline");
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", "#16ec98");
    poly.setAttribute("stroke-width", "2");
    poly.setAttribute("points", ptsAttr);
    svg.appendChild(poly);
  }

  vals.forEach((v, i) => {
    const [cx, cy] = toXY(i, v);
    const dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", cx);
    dot.setAttribute("cy", cy);
    dot.setAttribute("r", i === vals.length - 1 ? "3.5" : "2");
    dot.setAttribute("fill", i === vals.length - 1 ? "#e6eef0" : "#16ec98");
    svg.appendChild(dot);
  });
}

function renderRanking(dash) {
  const rnk = dash?.ranking;
  const badge = el("nav-rank-badge");
  if (!rnk) {
    if (badge) badge.textContent = "#—";
    return;
  }

  const cur = rnk.current || {};
  const az = cur.attack_zone || {};
  const rates = rnk.rates || {};
  const trend = rnk.observed_trend || {};

  const srvRank = finiteNum(cur.server_rank, null);
  const srvPts = finiteNum(cur.server_points, null);

  if (badge) {
    badge.textContent = cur.server_rank != null ? (srvRank !== null ? `#${srvRank}` : "#—") : az.label || "—";
  }

  const srvRankTxt = cur.server_rank != null ? (srvRank !== null ? `#${fmtInt(srvRank)}` : "#—") : "Rank pending";
  const srvPtsTxt = cur.server_points != null ? (srvPts !== null ? `${fmtInt(srvPts)} pts` : "— pts") : "— pts";
  el("rnk-server-pos").textContent = `${srvRankTxt} · ${srvPtsTxt}`;
  const estPts = finiteNum(cur.estimated_points, 0);
  const cleanEstPts = Math.abs(estPts) < 0.05 ? 0 : estPts;
  el("rnk-est-pts").textContent = `~${cleanEstPts.toFixed(1)} est. score (bld/res/flt)`;

  el("rnk-az-label").textContent = az.label || "—";
  const shield = cur.newbie_protected ? `Shielded (<10k · ${cur.newbie_eta_compound || "—"})` : "Open AZ";
  el("rnk-az-range").textContent = `${az.range_text || ""} · ${shield}`;

  const steadyRate = finiteNum(rates.pts_per_hr_steady, 0);
  const netResRate = finiteNum(rates.net_resources_per_hr, 0);
  const spdFactor = finiteNum(rates.speedup_factor, 1);
  const cleanSteady = Math.abs(steadyRate) < 0.005 ? 0 : steadyRate;
  el("rnk-rate-hr").textContent = `${cleanSteady >= 0 ? "+" : ""}${cleanSteady.toFixed(2)} pts/hr`;
  el("rnk-res-hr").textContent = `${fmtInt(Math.round(netResRate))} res/hr · x${spdFactor}`;

  const estPtsRaw = finiteNum(cur.estimated_points, null);
  const bldPts = finiteNum(cur.buildings_points, null);
  const resPts = finiteNum(cur.research_points, null);
  let bPct = 0;
  let rPct = 0;
  if (estPtsRaw !== null && estPtsRaw > 0 && bldPts !== null && resPts !== null) {
    bPct = clampPct(Math.round((bldPts / estPtsRaw) * 100));
    rPct = clampPct(Math.round((resPts / estPtsRaw) * 100));
    if (bPct + rPct > 100) {
      rPct = Math.max(0, 100 - bPct);
    }
  }
  const fPct = Math.max(0, 100 - bPct - rPct);
  el("rnk-breakdown").textContent = `Bld ${bPct}% · Res ${rPct}% · Flt ${fPct}%`;
  const spiceVal = finiteNum(cur.spice_value, 0);
  el("rnk-spice-val").textContent = `${fmtInt(Math.round(spiceVal))} Spice Eq`;

  const fmtSignedDeltaInt = (val) => {
    const rounded = Math.round(val);
    const clean = rounded === 0 ? 0 : rounded;
    return `${clean >= 0 ? "+" : ""}${fmtInt(clean)}`;
  };
  const hBody = el("rnk-horizons-tbody");
  const horizons = rnk.horizons || [];
  hBody.replaceChildren(
    ...(horizons.length === 0
      ? []
      : horizons.map((row) => {
          const tr = document.createElement("tr");
          const tdL = document.createElement("td");
          tdL.textContent = row.label;
          const tdS = document.createElement("td");
          tdS.className = "col-right tabular";
          const sp = finiteNum(row.steady_points, null);
          const sd = finiteNum(row.steady_delta, null);
          tdS.textContent = sp !== null && sd !== null ? `${fmtInt(Math.round(sp))} (${fmtSignedDeltaInt(sd)})` : "—";
          const tdSAz = document.createElement("td");
          tdSAz.className = "col-right tabular";
          tdSAz.textContent = row.steady_az;
          const tdC = document.createElement("td");
          tdC.className = "col-right tabular";
          const cp = finiteNum(row.compound_points, null);
          const cd = finiteNum(row.compound_delta, null);
          tdC.textContent = cp !== null && cd !== null ? `${fmtInt(Math.round(cp))} (${fmtSignedDeltaInt(cd)})` : "—";
          const tdCAz = document.createElement("td");
          tdCAz.className = "col-right tabular";
          tdCAz.textContent = row.compound_az;
          tr.append(tdL, tdS, tdSAz, tdC, tdCAz);
          return tr;
        }))
  );

  const mBody = el("rnk-milestones-tbody");
  const miles = rnk.milestones || [];
  mBody.replaceChildren(
    ...(miles.length === 0
      ? []
      : miles.map((m) => {
          const tr = document.createElement("tr");
          const tdN = document.createElement("td");
          const tgtPts = finiteNum(m.target_points, null);
          tdN.textContent = `${m.label} (${tgtPts !== null ? fmtInt(tgtPts) : "—"})`;
          const tdM = document.createElement("td");
          tdM.className = "col-right tabular";
          const missPts = finiteNum(m.missing_points, null);
          tdM.textContent = missPts !== null ? `+${fmtInt(Math.round(missPts))}` : "—";
          const tdSE = document.createElement("td");
          tdSE.className = "col-right tabular";
          tdSE.textContent = m.steady_eta;
          const tdCE = document.createElement("td");
          tdCE.className = "col-right tabular";
          tdCE.textContent = m.compound_eta;
          tr.append(tdN, tdM, tdSE, tdCE);
          return tr;
        }))
  );

  const count = finiteNum(trend.sample_count, 0);
  const deltaPts = finiteNum(trend.delta_points, null);
  const obsRate = finiteNum(trend.observed_pts_per_hr, null);
  const elapsedHrs = finiteNum(trend.elapsed_hours, null);
  const rankClimbed = finiteNum(trend.rank_climbed, null);
  el("rnk-trend-meta").textContent = `${count} sample${count === 1 ? "" : "s"}`;
  if (count >= 2 && deltaPts !== null) {
    const cleanDelta = deltaPts === 0 ? 0 : deltaPts;
    const deltaSign = cleanDelta >= 0 ? "+" : "";
    const cleanObsRate = obsRate !== null ? (obsRate === 0 ? 0 : obsRate) : null;
    const rateSign = cleanObsRate !== null && cleanObsRate >= 0 ? "+" : "";
    let rankStr = "";
    if (rankClimbed !== null) {
      const cleanRank = rankClimbed === 0 ? 0 : rankClimbed;
      if (cleanRank > 0) {
        rankStr = ` · Rank climbed +${fmtInt(cleanRank)}`;
      } else if (cleanRank < 0) {
        rankStr = ` · Rank dropped ${fmtInt(Math.abs(cleanRank))}`;
      } else {
        rankStr = " · Rank unchanged (0)";
      }
    }
    el("rnk-observed-rate").textContent =
      cleanObsRate !== null ? `${rateSign}${cleanObsRate} pts/hr observed` : `${deltaSign}${cleanDelta} pts`;
    el("rnk-observed-summary").textContent =
      `Window ${elapsedHrs !== null ? elapsedHrs : "—"}h: ${deltaSign}${cleanDelta} pts${rankStr}.`;
  } else {
    el("rnk-observed-rate").textContent = "Collecting samples...";
    el("rnk-observed-summary").textContent =
      "Rate delta activates automatically after 2 distinct observations.";
  }

  renderRankingChart(trend.samples || []);
}

function formatDbSummary(db) {
  if (!db) return "SQLite database disabled (no persistent state directory configured).";
  const sch = db.schema || null;
  const schemaLine = sch
    ? `Schema Version: ${sch.version_tag || `V00${sch.version || 0}`} / ${sch.target_version_tag || `V00${sch.target_version || 0}`} (${sch.up_to_date ? "UP-TO-DATE" : `${sch.pending_count || 0} PENDING`}) · Checksum=${String(sch.schema_checksum || "").slice(0, 12) || "n/a"}${Array.isArray(sch.checksum_drift) && sch.checksum_drift.length > 0 ? ` · DRIFT(${sch.checksum_drift.length})` : ""}`
    : "Schema Version: legacy";
  const lines = [
    `DB Path: ${db.db_path} (${fmtInt(db.size_bytes)} bytes)`,
    schemaLine,
    `Row Counts: ${Object.entries(db.counts || {}).map(([k, v]) => `${k}=${v}`).join(" · ")}`,
  ];
  if (sch && Array.isArray(sch.applied_migrations) && sch.applied_migrations.length > 0) {
    lines.push("", "== Applied Schema Migrations ==");
    for (const m of sch.applied_migrations) {
      lines.push(`${m.version_tag} — ${m.name} (${fmtShortDateTime(m.applied_ts)})`);
    }
  }
  lines.push("", "== Tutorial Progress ==");
  for (const t of db.tutorial_history || []) {
    const status = t.completed_ts ? "DONE" : t.can_complete ? "READY TO CLAIM" : "ACTIVE";
    const tStr = (t.tasks || []).map((x) => x.description).join(" | ") || "(no tasks parsed)";
    lines.push(`Step ${t.step}/${t.total_steps} [${status}] ${tStr}`);
  }
  if ((db.tutorial_history || []).length === 0) lines.push("(no tutorial rows recorded)");
  lines.push("", "== Recent Actions ==");
  for (const a of db.recent_actions || []) {
    const mode = a.dry_run ? "DRY" : a.executed ? "EXEC" : "SKIP";
    lines.push(`[${mode}] ${a.kind} ${a.target_name || a.command}${a.target_level != null ? ` -> ${a.target_level}` : ""} @ ${a.planet_coords || "-"} — ${a.message || ""}`);
  }
  return lines.join("\n");
}

function renderLogs(logs, dbSummary) {
  const pre = el("logs-pre");
  if (state.activeLogSource === "sqlite") {
    pre.textContent = formatDbSummary(dbSummary);
    return;
  }
  const src = state.activeLogSource === "game_api" ? logs?.game_api : logs?.commander;
  pre.textContent = src || "(Log file empty or not created yet.)";
}

function fmtShortDateTime(tsSec, fallbackTxt = "—") {
  const ts = finiteNum(tsSec, null);
  if (ts !== null && ts > 0) {
    const d = new Date(ts * 1000);
    if (!Number.isNaN(d.getTime())) {
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const dd = String(d.getDate()).padStart(2, "0");
      const hh = String(d.getHours()).padStart(2, "0");
      const mi = String(d.getMinutes()).padStart(2, "0");
      return `${mm}-${dd} ${hh}:${mi}`;
    }
  }
  return fallbackTxt && String(fallbackTxt).trim() ? String(fallbackTxt).trim() : "—";
}

function fmtDurationCountdown(secVal) {
  const sec = Math.max(0, Math.round(finiteNum(secVal, 0)));
  if (sec <= 0) return "Expired";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function formatUnitDictShort(obj, limit = 4) {
  if (!obj || typeof obj !== "object") return "None";
  const entries = Object.entries(obj)
    .map(([k, v]) => [String(k), Math.round(finiteNum(v, 0))])
    .filter(([, v]) => v > 0);
  if (entries.length === 0) return "None";
  const head = entries
    .slice(0, limit)
    .map(([k, v]) => `${k}:${fmtInt(v)}`)
    .join(" · ");
  return entries.length > limit ? `${head} (+${entries.length - limit})` : head;
}

function gradeClassFor(gradeRaw) {
  const g = String(gradeRaw || "?").trim().toUpperCase();
  if (g === "S") return "grade-s";
  if (g === "A+") return "grade-aplus";
  if (g === "A") return "grade-a";
  if (g === "B") return "grade-b";
  if (g === "C") return "grade-c";
  if (g === "D") return "grade-d";
  return "grade-unknown";
}

function updateIntelNavBadges(dash) {
  const counts = dash?.db_summary?.counts || {};
  const spyBadge = el("nav-spy-badge");
  if (spyBadge) {
    const spyCnt = state.espData?.summary?.spied_targets ?? counts.spy_reports ?? counts.spy_targets ?? 0;
    spyBadge.textContent = String(Math.max(0, Math.round(finiteNum(spyCnt, 0))));
  }
  const colBadge = el("nav-col-badge");
  if (colBadge) {
    const colCnt = state.colonyData
      ? state.colonyData.qualified_count ?? state.colonyData.probed_count ?? 0
      : counts.galaxy_slots ?? 0;
    colBadge.textContent = String(Math.max(0, Math.round(finiteNum(colCnt, 0))));
  }
  const atkBadge = el("nav-atk-badge");
  if (atkBadge) {
    const atkCnt = state.atkData?.summary?.total_battles ?? counts.battle_reports ?? 0;
    atkBadge.textContent = String(Math.max(0, Math.round(finiteNum(atkCnt, 0))));
  }
  const recBadge = el("nav-rec-badge");
  if (recBadge) {
    const recCnt = state.recData?.summary?.total_runs ?? counts.recycle_reports ?? 0;
    recBadge.textContent = String(Math.max(0, Math.round(finiteNum(recCnt, 0))));
  }
}

async function syncAllInboxFolders() {
  try {
    showFeedback("Syncing all in-game message folders (Espionage, Battle, Fleet, User, Alliance, System)...", "ok");
    const res = await apiCommand({ command: "sync_messages" });
    const fc = res.folder_counts || {};
    const folderSummary = Object.entries(fc)
      .map(([k, v]) => `${k}:${finiteNum(v?.total, 0)}`)
      .join(" · ");
    const archived = Math.round(finiteNum(res.archived_messages, 0));
    const spyCnt = Math.round(finiteNum(res.synced_reports, 0));
    const delNote = res.deleted_in_game ? "deleted in-game" : "stored in SQLite";
    showFeedback(
      `Inbox sync complete: ${archived} archived (${spyCnt} spy) — ${delNote}${folderSummary ? ` [${folderSummary}]` : ""}.`,
      "ok"
    );
    await loadDashboard({ refresh: false, force: true });
    if (state.activePage === "espionage") await refreshEspionageView();
    else if (state.activePage === "colonies") await refreshColoniesView();
    else if (state.activePage === "attacks") await refreshAttacksView();
    else if (state.activePage === "recyclers") await refreshRecyclersView();
  } catch (err) {
    showFeedback(`Inbox sync failed: ${err.message}`, feedbackKindForError(err));
  }
}

async function refreshEspionageView() {
  try {
    const data = await apiCommand({ command: "espionage_intel", limit: 200 });
    state.espData = data;
    renderEspionageView(data);
    updateIntelNavBadges(state.dashboard);
  } catch (err) {
    showFeedback(`Espionage data load failed: ${err.message}`, feedbackKindForError(err));
  }
}

function renderEspionageView(data) {
  if (!data) return;
  const sum = data.summary || {};
  const targets = Array.isArray(data.targets) ? data.targets : [];
  const reports = Array.isArray(data.reports) ? data.reports : [];

  if (el("esp-kpi-targets")) {
    el("esp-kpi-targets").textContent = `${fmtInt(sum.spied_targets ?? 0)} / ${fmtInt(sum.total_targets ?? targets.length)}`;
  }
  if (el("esp-kpi-reports")) {
    el("esp-kpi-reports").textContent = `${fmtInt(sum.total_reports ?? reports.length)} archived spy reports`;
  }
  if (el("esp-kpi-spacia")) {
    el("esp-kpi-spacia").textContent = `${fmtInt(sum.spacia_count ?? 0)} Spacia`;
  }
  if (el("esp-kpi-inactives")) {
    el("esp-kpi-inactives").textContent = `${fmtInt(sum.inactive_count ?? 0)} yellow inactive planets`;
  }
  if (el("esp-kpi-debris")) {
    el("esp-kpi-debris").textContent = `${fmtInt(sum.debris_count ?? 0)} Debris`;
  }
  const bestSpEq = targets.reduce((acc, t) => Math.max(acc, finiteNum(t.spice_eq, 0)), 0);
  if (el("esp-kpi-top-loot")) {
    el("esp-kpi-top-loot").textContent = `Peak target: ${fmtInt(Math.round(bestSpEq))} SpEq`;
  }
  if (el("esp-meta-count")) {
    el("esp-meta-count").textContent = `${targets.length} TARGETS · ${reports.length} REPORTS`;
  }

  const tBody = el("esp-targets-tbody");
  if (tBody) {
    if (targets.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 8;
      td.className = "empty-cell";
      td.textContent = "No espionage targets recorded yet. Run Scan Galaxy or Sync All Folders.";
      tr.appendChild(td);
      tBody.replaceChildren(tr);
    } else {
      const planet = activePlanetFrom(state.dashboard);
      tBody.replaceChildren(
        ...targets.map((t) => {
          const tr = document.createElement("tr");
          const coords = String(t.coords || t.target_coords || "—");

          const tdC = document.createElement("td");
          tdC.className = "tabular";
          tdC.textContent = coords;

          const tdOwn = document.createElement("td");
          tdOwn.textContent = [t.planet_name, t.owner].filter(Boolean).join(" · ") || "Uninhabited / Unknown";

          const tdSt = document.createElement("td");
          const stSpan = document.createElement("span");
          const isSpacia = Boolean(t.is_spacia);
          const isInact = Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
          stSpan.className = `status-word${isSpacia || isInact ? "" : " is-warn"}`;
          const tags = [isSpacia ? "SPACIA" : isInact ? "INACTIVE" : String(t.status_color || "ACTIVE").toUpperCase()];
          if (t.has_debris) tags.push("DEBRIS");
          if (t.dug_in) tags.push("DUG-IN");
          stSpan.textContent = tags.join(" · ");
          tdSt.appendChild(stSpan);

          const tdF = document.createElement("td");
          tdF.className = "col-right tabular";
          const uf = finiteNum(t.uninhabited_fields, null);
          tdF.textContent = uf !== null && uf > 0 ? String(Math.round(uf)) : "—";

          const tdRes = document.createElement("td");
          tdRes.className = "tabular";
          tdRes.textContent = t.has_spy_intel ? formatResourcesInline(t.resources) : "Not spied yet";

          const tdSp = document.createElement("td");
          tdSp.className = "col-right tabular";
          tdSp.textContent = t.has_spy_intel ? fmtInt(Math.round(finiteNum(t.spice_eq, 0))) : "—";

          const tdFD = document.createElement("td");
          tdFD.className = "tabular";
          if (t.has_spy_intel) {
            const fStr = formatUnitDictShort(t.fleet, 3);
            const dStr = formatUnitDictShort(t.defense, 3);
            tdFD.textContent = fStr === "None" && dStr === "None" ? "Clear (0)" : `F:${fStr} / D:${dStr}`;
          } else {
            tdFD.textContent = "—";
          }

          const tdOps = document.createElement("td");
          tdOps.className = "col-right";
          const wrap = document.createElement("div");
          wrap.className = "row-actions";

          const spyBtn = document.createElement("button");
          spyBtn.type = "button";
          spyBtn.className = "btn btn-secondary btn-sm";
          spyBtn.textContent = "Spy";
          spyBtn.addEventListener("click", async () => {
            try {
              showFeedback(`Dispatching spy probe to ${coords}...`, "ok");
              const res = await apiCommand({
                command: "spy",
                target_coords: coords,
                quantity: 1,
                planet_id: planet?.planet_id,
                dry_run: false,
              });
              showFeedback(res.message || `Spy probe sent to ${coords}.`, res.executed ? "ok" : "warn");
              await refreshEspionageView();
            } catch (err) {
              showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
            }
          });

          const atkBtn = document.createElement("button");
          atkBtn.type = "button";
          atkBtn.className = "btn btn-primary btn-sm";
          atkBtn.textContent = "Attack";
          atkBtn.addEventListener("click", async () => {
            try {
              showFeedback(`Dispatching raid to ${coords}...`, "ok");
              const res = await apiCommand({
                command: "attack",
                target_coords: coords,
                planet_id: planet?.planet_id,
                dry_run: false,
              });
              showFeedback(res.message || `Attack processed for ${coords}.`, res.executed ? "ok" : "warn");
              await loadDashboard({ refresh: false, force: true });
              await refreshEspionageView();
            } catch (err) {
              showFeedback(`Attack failed: ${err.message}`, feedbackKindForError(err));
            }
          });

          wrap.append(spyBtn, atkBtn);
          tdOps.appendChild(wrap);
          tr.append(tdC, tdOwn, tdSt, tdF, tdRes, tdSp, tdFD, tdOps);
          return tr;
        })
      );
    }
  }

  const rBody = el("esp-reports-tbody");
  if (rBody) {
    if (reports.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 7;
      td.className = "empty-cell";
      td.textContent = "No historical spy reports stored yet.";
      tr.appendChild(td);
      rBody.replaceChildren(tr);
    } else {
      rBody.replaceChildren(
        ...reports.map((r) => {
          const tr = document.createElement("tr");
          const tdTime = document.createElement("td");
          tdTime.className = "tabular";
          tdTime.textContent = fmtShortDateTime(r.ts, r.msg_date_text);

          const tdC = document.createElement("td");
          tdC.className = "tabular";
          tdC.textContent = String(r.coords || "—");

          const tdOwn = document.createElement("td");
          tdOwn.textContent = [r.planet_name, r.owner].filter(Boolean).join(" · ") || (r.uninhabited_fields ? "Uninhabited Slot" : "Unknown");

          const tdF = document.createElement("td");
          tdF.className = "col-right tabular";
          const uf = finiteNum(r.uninhabited_fields, null);
          tdF.textContent = uf !== null && uf > 0 ? `${Math.round(uf)} fields` : "—";

          const tdRes = document.createElement("td");
          tdRes.className = "tabular";
          const spEq = Math.round(finiteNum(r.spice_eq ?? r.loot_spice_eq, 0));
          tdRes.textContent = `${formatResourcesInline(r.resources)} (${fmtInt(spEq)} SpEq)`;

          const tdBld = document.createElement("td");
          tdBld.className = "tabular";
          const bStr = formatUnitDictShort(r.buildings, 3);
          const rStr = formatUnitDictShort(r.researches, 3);
          tdBld.textContent = bStr === "None" && rStr === "None" ? "—" : `B:${bStr} · R:${rStr}`;

          const tdFlt = document.createElement("td");
          tdFlt.className = "tabular";
          const fStr = formatUnitDictShort(r.fleet, 3);
          const dStr = formatUnitDictShort(r.defense, 3);
          tdFlt.textContent = fStr === "None" && dStr === "None" ? "Clear (0)" : `F:${fStr} · D:${dStr}`;

          tr.append(tdTime, tdC, tdOwn, tdF, tdRes, tdBld, tdFlt);
          return tr;
        })
      );
    }
  }
}

async function refreshColoniesView() {
  try {
    const data = await apiCommand({ command: "colony_candidates", limit: 200 });
    state.colonyData = data;
    renderColoniesView(data);
    updateIntelNavBadges(state.dashboard);
  } catch (err) {
    showFeedback(`Colony grading load failed: ${err.message}`, feedbackKindForError(err));
  }
}

function renderColoniesView(data) {
  if (!data) return;
  const gc = data.grade_counts || {};
  const allCands = Array.isArray(data.candidates) ? data.candidates : [];

  if (el("col-kpi-qualified")) {
    el("col-kpi-qualified").textContent = `${fmtInt(data.qualified_count ?? 0)} Ready`;
  }
  if (el("col-kpi-grades")) {
    el("col-kpi-grades").textContent = `S:${gc.S || 0} · A+:${gc["A+"] || 0} · A:${gc.A || 0} · B:${gc.B || 0} · C/D:${(gc.C || 0) + (gc.D || 0)}`;
  }
  if (el("col-kpi-probed")) {
    el("col-kpi-probed").textContent = `${fmtInt(data.probed_count ?? 0)} Spied`;
  }
  if (el("col-kpi-unprobed")) {
    el("col-kpi-unprobed").textContent = `${fmtInt(data.unprobed_count ?? 0)} unprobed free slots`;
  }
  if (el("col-kpi-ships")) {
    el("col-kpi-ships").textContent = `${fmtInt(data.colony_ships ?? 0)} s8 · ${fmtInt(data.spy_probes ?? 0)} s10`;
  }
  if (el("col-kpi-origin")) {
    el("col-kpi-origin").textContent = `Origin [${data.origin_coords || "--"}] · ${fmtInt(data.planet_count ?? 1)} colonies`;
  }
  if (el("col-min-fields") && data.min_colony_fields != null) {
    el("col-min-fields").value = String(data.min_colony_fields);
  }
  if (el("col-allow-colonize") && data.allow_colonize != null) {
    el("col-allow-colonize").checked = Boolean(data.allow_colonize);
  }

  let filtered = allCands;
  if (state.colonyFilter === "probed") {
    filtered = allCands.filter((c) => Boolean(c.probed));
  } else if (state.colonyFilter === "qualified") {
    filtered = allCands.filter((c) => Boolean(c.meets_target));
  } else if (state.colonyFilter === "unprobed") {
    filtered = allCands.filter((c) => !c.probed);
  }

  if (el("col-meta-count")) {
    el("col-meta-count").textContent = `${filtered.length} / ${allCands.length} SLOTS`;
  }

  const tbody = el("col-candidates-tbody");
  if (!tbody) return;
  if (filtered.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.className = "empty-cell";
    td.textContent = "No matching colony slots. Click Scan Galaxy or probe free slots 7-12.";
    tr.appendChild(td);
    tbody.replaceChildren(tr);
    return;
  }

  const planet = activePlanetFrom(state.dashboard);
  tbody.replaceChildren(
    ...filtered.map((c) => {
      const tr = document.createElement("tr");
      const coords = String(c.coords || "—");
      const grade = String(c.grade || "?");

      const tdGrade = document.createElement("td");
      const gBadge = document.createElement("span");
      gBadge.className = `grade-badge ${gradeClassFor(grade)}`;
      gBadge.textContent = grade;
      tdGrade.appendChild(gBadge);

      const tdScore = document.createElement("td");
      tdScore.className = "col-right tabular";
      tdScore.textContent = `${Math.round(finiteNum(c.score, 0))}/100`;

      const tdCoords = document.createElement("td");
      tdCoords.className = "tabular";
      tdCoords.textContent = coords;

      const tdZone = document.createElement("td");
      tdZone.textContent = `Pos ${c.position || "?"} · ${c.orbital_band || "Unknown"}`;

      const tdFields = document.createElement("td");
      tdFields.className = "col-right tabular";
      const uf = finiteNum(c.uninhabited_fields, null);
      if (uf !== null && uf > 0) {
        const fSpan = document.createElement("span");
        fSpan.className = `status-word${c.meets_target ? "" : " is-warn"}`;
        fSpan.textContent = `${Math.round(uf)} fields`;
        tdFields.appendChild(fSpan);
      } else {
        tdFields.textContent = "Unprobed";
      }

      const tdDist = document.createElement("td");
      tdDist.className = "col-right tabular";
      const sysDist = Math.round(finiteNum(c.system_distance, 0));
      tdDist.textContent = sysDist === 0 ? "Same sys" : `${sysDist} sys`;

      const tdReason = document.createElement("td");
      tdReason.textContent = String(c.reason || "");

      const tdAct = document.createElement("td");
      tdAct.className = "col-right";
      const wrap = document.createElement("div");
      wrap.className = "row-actions";

      const probeBtn = document.createElement("button");
      probeBtn.type = "button";
      probeBtn.className = "btn btn-secondary btn-sm";
      probeBtn.textContent = c.probed ? "Re-Probe" : "Probe";
      probeBtn.addEventListener("click", async () => {
        try {
          showFeedback(`Probing free slot ${coords} for field count...`, "ok");
          const res = await apiCommand({
            command: "spy",
            target_coords: coords,
            quantity: 1,
            planet_id: planet?.planet_id,
            dry_run: false,
          });
          showFeedback(res.message || `Spy probe dispatched to ${coords}.`, res.executed ? "ok" : "warn");
          await refreshColoniesView();
        } catch (err) {
          showFeedback(`Probe failed: ${err.message}`, feedbackKindForError(err));
        }
      });
      wrap.appendChild(probeBtn);

      if (c.probed) {
        const colBtn = document.createElement("button");
        colBtn.type = "button";
        colBtn.className = c.meets_target ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm";
        colBtn.textContent = "Colonize";
        colBtn.addEventListener("click", async () => {
          try {
            showFeedback(`Dispatching Colony ship (s8) to ${coords}...`, "ok");
            const res = await apiCommand({
              command: "colonize",
              target_coords: coords,
              planet_id: planet?.planet_id,
              dry_run: false,
            });
            showFeedback(res.message || `Colonization processed for ${coords}.`, res.executed ? "ok" : "warn");
            await loadDashboard({ refresh: false, force: true });
            await refreshColoniesView();
          } catch (err) {
            showFeedback(`Colonize failed: ${err.message}`, feedbackKindForError(err));
          }
        });
        wrap.appendChild(colBtn);
      }

      tdAct.appendChild(wrap);
      tr.append(tdGrade, tdScore, tdCoords, tdZone, tdFields, tdDist, tdReason, tdAct);
      return tr;
    })
  );
}

async function refreshAttacksView() {
  try {
    const data = await apiCommand({ command: "attack_results", limit: 200 });
    state.atkData = data;
    renderAttacksView(data);
    updateIntelNavBadges(state.dashboard);
  } catch (err) {
    showFeedback(`Attack results load failed: ${err.message}`, feedbackKindForError(err));
  }
}

function renderAttacksView(data) {
  if (!data) return;
  const sum = data.summary || {};
  const rStats = data.raid_stats || {};
  const battles = Array.isArray(data.battles) ? data.battles : [];
  const blocks = Array.isArray(data.blocked_targets) ? data.blocked_targets : [];

  if (el("atk-kpi-total")) {
    el("atk-kpi-total").textContent = `${fmtInt(sum.total_battles ?? battles.length)} Battles`;
  }
  if (el("atk-kpi-wl")) {
    el("atk-kpi-wl").textContent = `W ${sum.wins ?? 0} · L ${sum.losses ?? 0} · D ${sum.draws ?? 0}`;
  }
  if (el("atk-kpi-loot")) {
    el("atk-kpi-loot").textContent = `${fmtInt(Math.round(finiteNum(sum.total_loot_spice_eq, 0)))} SpEq`;
  }
  if (el("atk-kpi-loss")) {
    el("atk-kpi-loss").textContent = `Attacker losses: ${fmtInt(Math.round(finiteNum(sum.total_losses_spice_eq, 0)))} SpEq`;
  }
  const raids24 = Math.round(finiteNum(rStats.total_24h, 0));
  if (el("atk-kpi-raids24")) {
    el("atk-kpi-raids24").textContent = `${fmtInt(raids24)} / 24h`;
  }
  if (el("atk-kpi-blocks")) {
    el("atk-kpi-blocks").textContent = `${fmtInt(sum.active_blocks_count ?? 0)} active bash timeouts`;
  }
  if (el("atk-meta-count")) {
    el("atk-meta-count").textContent = `${battles.length} REPORTS · ${blocks.length} BLOCKS`;
  }

  const bBody = el("atk-battles-tbody");
  if (bBody) {
    if (battles.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 8;
      td.className = "empty-cell";
      td.textContent = "No battle reports archived yet. Click Sync All Folders to import reports.";
      tr.appendChild(td);
      bBody.replaceChildren(tr);
    } else {
      bBody.replaceChildren(
        ...battles.map((b) => {
          const tr = document.createElement("tr");
          const tdTime = document.createElement("td");
          tdTime.className = "tabular";
          tdTime.textContent = fmtShortDateTime(b.ts, b.msg_date_text);

          const tdC = document.createElement("td");
          tdC.className = "tabular";
          tdC.textContent = String(b.coords || "—");

          const tdDef = document.createElement("td");
          tdDef.textContent = String(b.defender_name || b.subject || "Unknown");

          const tdOut = document.createElement("td");
          const outBadge = document.createElement("span");
          const outVal = String(b.outcome || "draw").toLowerCase();
          outBadge.className = `outcome-badge ${outVal === "win" ? "is-win" : outVal === "loss" ? "is-loss" : "is-draw"}`;
          outBadge.textContent = outVal.toUpperCase();
          tdOut.appendChild(outBadge);

          const tdRounds = document.createElement("td");
          tdRounds.className = "col-right tabular";
          tdRounds.textContent = String(Math.round(finiteNum(b.rounds, 0)));

          const tdLoot = document.createElement("td");
          tdLoot.className = "tabular";
          const lootSp = Math.round(finiteNum(b.loot?.spice_eq, 0));
          tdLoot.textContent = lootSp > 0 ? `${formatResourcesInline(b.loot)} (+${fmtInt(lootSp)} SpEq)` : "0";

          const tdLoss = document.createElement("td");
          tdLoss.className = "tabular";
          const atkLoss = Math.round(finiteNum(b.attacker_losses?.spice_eq, 0));
          const defLoss = Math.round(finiteNum(b.defender_losses?.spice_eq, 0));
          tdLoss.textContent = `Atk ${fmtInt(atkLoss)} / Def ${fmtInt(defLoss)} SpEq`;

          const tdDeb = document.createElement("td");
          tdDeb.className = "tabular";
          const dPig = Math.round(finiteNum(b.debris?.pig_iron, 0));
          const dMet = Math.round(finiteNum(b.debris?.metal, 0));
          tdDeb.textContent = dPig > 0 || dMet > 0 ? `${fmtInt(dPig)} Pig · ${fmtInt(dMet)} Met` : "None";

          tr.append(tdTime, tdC, tdDef, tdOut, tdRounds, tdLoot, tdLoss, tdDeb);
          return tr;
        })
      );
    }
  }

  const blkBody = el("atk-blocks-tbody");
  if (blkBody) {
    if (blocks.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "empty-cell";
      td.textContent = "No active bash protection or server blocks recorded.";
      tr.appendChild(td);
      blkBody.replaceChildren(tr);
    } else {
      const planet = activePlanetFrom(state.dashboard);
      blkBody.replaceChildren(
        ...blocks.map((bt) => {
          const tr = document.createElement("tr");
          const coords = String(bt.coords || "—");

          const tdC = document.createElement("td");
          tdC.className = "tabular";
          tdC.textContent = coords;

          const tdOwn = document.createElement("td");
          tdOwn.textContent = [bt.planet_name, bt.owner].filter(Boolean).join(" · ") || "Unknown";

          const tdHits = document.createElement("td");
          tdHits.className = "col-right tabular";
          tdHits.textContent = String(Math.round(finiteNum(bt.bash_hits, 0)));

          const tdRem = document.createElement("td");
          tdRem.className = "col-right tabular";
          tdRem.textContent = bt.active_block ? fmtDurationCountdown(bt.remaining_seconds) : "Cleared";

          const tdRsn = document.createElement("td");
          tdRsn.textContent = String(bt.block_reason || "Bash / Server protection");

          const tdOps = document.createElement("td");
          tdOps.className = "col-right";
          const spyBtn = document.createElement("button");
          spyBtn.type = "button";
          spyBtn.className = "btn btn-secondary btn-sm";
          spyBtn.textContent = "Spy";
          spyBtn.addEventListener("click", async () => {
            try {
              const res = await apiCommand({
                command: "spy",
                target_coords: coords,
                quantity: 1,
                planet_id: planet?.planet_id,
                dry_run: false,
              });
              showFeedback(res.message || `Spy probe sent to ${coords}.`, res.executed ? "ok" : "warn");
            } catch (err) {
              showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
            }
          });
          tdOps.appendChild(spyBtn);

          tr.append(tdC, tdOwn, tdHits, tdRem, tdRsn, tdOps);
          return tr;
        })
      );
    }
  }
}

async function refreshRecyclersView() {
  try {
    const data = await apiCommand({
      command: "recycler_results",
      category: state.emailCatFilter,
      limit: 200,
    });
    state.recData = data;
    renderRecyclersView(data);
    updateIntelNavBadges(state.dashboard);
  } catch (err) {
    showFeedback(`Recycler and email load failed: ${err.message}`, feedbackKindForError(err));
  }
}

function renderRecyclersView(data) {
  if (!data) return;
  const sum = data.summary || {};
  const recycles = Array.isArray(data.recycles) ? data.recycles : [];
  const debris = Array.isArray(data.debris_targets) ? data.debris_targets : [];
  const emails = Array.isArray(data.other_emails) ? data.other_emails : [];
  const cats = sum.category_counts || {};

  if (el("rec-kpi-runs")) {
    el("rec-kpi-runs").textContent = `${fmtInt(sum.total_runs ?? recycles.length)} Harvests`;
  }
  if (el("rec-kpi-ships")) {
    el("rec-kpi-ships").textContent = `${fmtInt(sum.recyclers_owned ?? 0)} Recyclers (s11) owned`;
  }
  if (el("rec-kpi-speq")) {
    el("rec-kpi-speq").textContent = `${fmtInt(Math.round(finiteNum(sum.total_spice_eq, 0)))} SpEq`;
  }
  if (el("rec-kpi-raw")) {
    const tPig = Math.round(finiteNum(sum.total_pig_iron, 0));
    const tMet = Math.round(finiteNum(sum.total_metal, 0));
    const tKry = Math.round(finiteNum(sum.total_kryptonite, 0));
    const tSpi = Math.round(finiteNum(sum.total_spice, 0));
    if (tKry > 0 || tSpi > 0) {
      el("rec-kpi-raw").textContent = `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met · ${fmtInt(tKry)} Kry · ${fmtInt(tSpi)} Spi`;
    } else {
      el("rec-kpi-raw").textContent = `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met`;
    }
  }
  if (el("rec-kpi-debris")) {
    el("rec-kpi-debris").textContent = `${fmtInt(sum.active_debris_count ?? debris.length)} Fields`;
  }
  if (el("rec-kpi-emails")) {
    el("rec-kpi-emails").textContent = `${fmtInt(sum.other_email_count ?? emails.length)} Notices`;
  }
  if (el("rec-kpi-cats")) {
    const userCnt = Math.round(finiteNum(cats.general, 0));
    const retCnt = Math.round(finiteNum(cats.fleet_return, 0));
    el("rec-kpi-cats").textContent = `User ${userCnt} · Return ${retCnt}`;
  }
  if (el("rec-meta-count")) {
    el("rec-meta-count").textContent = `${recycles.length} HARVESTS · ${debris.length} DEBRIS · ${emails.length} EMAILS`;
  }

  const hBody = el("rec-harvests-tbody");
  if (hBody) {
    if (recycles.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 7;
      td.className = "empty-cell";
      td.textContent = "No recycler reports archived yet. Click Sync All Folders to import fleet reports.";
      tr.appendChild(td);
      hBody.replaceChildren(tr);
    } else {
      hBody.replaceChildren(
        ...recycles.map((r) => {
          const tr = document.createElement("tr");
          const tdTime = document.createElement("td");
          tdTime.className = "tabular";
          tdTime.textContent = fmtShortDateTime(r.ts, r.msg_date_text);

          const tdC = document.createElement("td");
          tdC.className = "tabular";
          tdC.textContent = String(r.coords || "—");

          const tdShips = document.createElement("td");
          tdShips.className = "col-right tabular";
          const rCnt = Math.round(finiteNum(r.recycler_count, 0));
          const rCap = Math.round(finiteNum(r.cargo_capacity, 0));
          tdShips.textContent = rCnt > 0 ? `${fmtInt(rCnt)}x (${fmtInt(rCap)})` : "—";

          const tdPig = document.createElement("td");
          tdPig.className = "col-right tabular";
          tdPig.textContent = fmtInt(r.harvested_pig_iron ?? 0);

          const tdMet = document.createElement("td");
          tdMet.className = "col-right tabular";
          tdMet.textContent = fmtInt(r.harvested_metal ?? 0);

          const tdSp = document.createElement("td");
          tdSp.className = "col-right tabular";
          tdSp.textContent = `${fmtInt(Math.round(finiteNum(r.spice_eq, 0)))} SpEq`;

          const tdBef = document.createElement("td");
          tdBef.className = "tabular";
          const bPig = Math.round(finiteNum(r.field_pig_iron, 0));
          const bMet = Math.round(finiteNum(r.field_metal, 0));
          const hKry = Math.round(finiteNum(r.harvested_kryptonite, 0));
          const hSpi = Math.round(finiteNum(r.harvested_spice, 0));
          if (hKry > 0 || hSpi > 0) {
            tdBef.textContent = `+${fmtInt(hKry)} Kry · +${fmtInt(hSpi)} Spi`;
          } else {
            tdBef.textContent = bPig > 0 || bMet > 0 ? `${fmtInt(bPig)} Pig · ${fmtInt(bMet)} Met` : "—";
          }

          tr.append(tdTime, tdC, tdShips, tdPig, tdMet, tdSp, tdBef);
          return tr;
        })
      );
    }
  }

  const dBody = el("rec-debris-tbody");
  if (dBody) {
    if (debris.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 5;
      td.className = "empty-cell";
      td.textContent = "No active floating debris fields spotted in scanned systems.";
      tr.appendChild(td);
      dBody.replaceChildren(tr);
    } else {
      const planet = activePlanetFrom(state.dashboard);
      dBody.replaceChildren(
        ...debris.map((d) => {
          const tr = document.createElement("tr");
          const coords = String(d.coords || "—");

          const tdC = document.createElement("td");
          tdC.className = "tabular";
          tdC.textContent = coords;

          const tdOwn = document.createElement("td");
          tdOwn.textContent = [d.planet_name, d.owner].filter(Boolean).join(" · ") || "Orbital Debris";

          const tdSt = document.createElement("td");
          const stBadge = document.createElement("span");
          stBadge.className = "status-word";
          stBadge.textContent = "RUBBLE FIELD";
          tdSt.appendChild(stBadge);

          const tdSeen = document.createElement("td");
          tdSeen.className = "col-right tabular";
          tdSeen.textContent = fmtShortDateTime(d.last_scanned_ts);

          const tdOps = document.createElement("td");
          tdOps.className = "col-right";
          const recBtn = document.createElement("button");
          recBtn.type = "button";
          recBtn.className = "btn btn-primary btn-sm";
          recBtn.textContent = "Recycle";
          recBtn.addEventListener("click", async () => {
            try {
              showFeedback(`Dispatching Recyclers (s11) to ${coords}...`, "ok");
              const res = await apiCommand({
                command: "recycle",
                target_coords: coords,
                planet_id: planet?.planet_id,
                dry_run: false,
              });
              showFeedback(res.message || `Recycler dispatched to ${coords}.`, res.executed ? "ok" : "warn");
              await refreshRecyclersView();
            } catch (err) {
              showFeedback(`Recycle failed: ${err.message}`, feedbackKindForError(err));
            }
          });
          tdOps.appendChild(recBtn);

          tr.append(tdC, tdOwn, tdSt, tdSeen, tdOps);
          return tr;
        })
      );
    }
  }

  const eBody = el("rec-emails-tbody");
  if (eBody) {
    if (emails.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "empty-cell";
      td.textContent = "No personal/alliance/admin emails or matching fleet notices archived.";
      tr.appendChild(td);
      eBody.replaceChildren(tr);
    } else {
      eBody.replaceChildren(
        ...emails.map((m) => {
          const tr = document.createElement("tr");
          const tdTime = document.createElement("td");
          tdTime.className = "tabular";
          tdTime.textContent = fmtShortDateTime(m.ts ?? m.ingested_ts, m.msg_date_text);

          const tdType = document.createElement("td");
          tdType.className = "tabular";
          tdType.textContent = `${m.folder_type || "inbox"} · ${m.category || "general"}`;

          const tdSender = document.createElement("td");
          tdSender.textContent = String(m.sender || "System");

          const tdSubj = document.createElement("td");
          tdSubj.textContent = String(m.subject || "—");

          const tdC = document.createElement("td");
          tdC.className = "tabular";
          tdC.textContent = String(m.coords || "—");

          const tdBody = document.createElement("td");
          tdBody.className = "msg-body-cell";
          const rawTxt = String(m.body_text || "").trim();
          tdBody.textContent = rawTxt.length > 260 ? `${rawTxt.slice(0, 260)}...` : rawTxt || "—";

          tr.append(tdTime, tdType, tdSender, tdSubj, tdC, tdBody);
          return tr;
        })
      );
    }
  }
}

function onPlanetChanged() {
  if (!state.dashboard) return;
  renderColonyMatrix(state.dashboard);
  renderTelemetry(state.dashboard);
  renderDashNearestEtas(state.dashboard);
  refreshBuildingsEtaForSelectedPlanet();
  if (state.activePage === "combat") {
    refreshCombatForSelectedPlanet({ refresh: false });
  } else if (state.activePage === "colonies") {
    refreshColoniesView();
  }
}

async function loadDashboard({ refresh = false, preserveConfig = true, force = false } = {}) {
  if (!refresh && !force && state.inFlightDashboard) return false;
  const reqSeq = ++state.dashboardSeq;
  state.inFlightDashboardCount += 1;
  state.inFlightDashboard = true;
  try {
    const dash = await apiGet(`/dashboard?refresh=${refresh ? 1 : 0}`, 15000);
    if (reqSeq !== state.dashboardSeq) return false;
    if (applyVersionSnapshot(dash)) return false;
    setApiStatus(true);
    state.dashboard = dash;
    if (dash.buildings_eta) {
      if (!state.selectedPlanetId || state.selectedPlanetId === dash.buildings_eta.planet?.planet_id) {
        state.buildingsEtaData = dash.buildings_eta;
      }
    }
    if (dash.combat) {
      if (!state.selectedPlanetId || state.selectedPlanetId === dash.combat.planet_id) {
        state.combatData = dash.combat;
      }
    }

    renderTopbar(dash);
    renderTelemetry(dash);
    renderGaNnHud(dash);
    renderColonyMatrix(dash);
    renderPlanTable(dash);
    renderDashNearestEtas(dash);
    renderBuildingsView(dash);
    renderCombatView(dash);
    renderRanking(dash);
    renderLogs(dash.logs, dash.db_summary);
    updateIntelNavBadges(dash);
    if (dash.notifications && typeof dash.notifications === "object") {
      syncNotificationsPayload(dash.notifications);
    }
    if (state.activePage === "espionage") {
      refreshEspionageView();
    } else if (state.activePage === "colonies") {
      refreshColoniesView();
    } else if (state.activePage === "attacks") {
      refreshAttacksView();
    } else if (state.activePage === "recyclers") {
      refreshRecyclersView();
    }
    if (!state.configDirty && !state.configSaving) {
      renderConfigForm(dash.config);
      state.configDirty = false;
    }
    if (!state.agSimDirty && !state.agSimRunning && !state.configSaving) {
      populateAgParamControls(dash.config);
      state.actionGraphData = dash.action_graph || buildClientFallbackGraph(dash);
      renderActionGraphView(state.actionGraphData);
    }
    return true;
  } catch (err) {
    if (reqSeq !== state.dashboardSeq) return false;
    if (isRateLimitError(err)) {
      setApiStatus(true);
      showFeedback(`SpaceInvasion rate-limit cooldown active: ${err.message}`, "warn");
    } else {
      const msg = err?.name === "AbortError" ? "timeout" : err?.message || "unreachable";
      setApiStatus(false, msg);
    }
    return false;
  } finally {
    state.inFlightDashboardCount = Math.max(0, state.inFlightDashboardCount - 1);
    state.inFlightDashboard = state.inFlightDashboardCount > 0;
  }
}

async function toggleBlastFurnaceRate(targetPct) {
  const planet = activePlanetFrom(state.dashboard);
  if (!planet) return;
  try {
    showFeedback(`Setting Blast furnace to ${targetPct}% on [${planet.coords}]...`, "ok");
    const res = await apiCommand({
      command: "throttle_building",
      building_id: 2,
      percentage: Number(targetPct),
      planet_id: planet.planet_id,
      dry_run: false,
    });
    showFeedback(res.message || `Blast furnace set to ${targetPct}%.`, "ok");
    await loadDashboard({ refresh: false, force: true });
    await refreshBuildingsEtaForSelectedPlanet(false);
  } catch (err) {
    showFeedback(`Throttle failed: ${err.message}`, feedbackKindForError(err));
  }
}

async function updateLoopMode(mode) {
  const val = mode === "auto" ? null : mode;
  try {
    await apiCommand({ command: "bot_control", mode_override: val });
    syncModeButtons(mode);
    await loadDashboard({ refresh: false, force: true });
    showFeedback(`Loop mode set to ${mode}.`, "ok");
  } catch (err) {
    showFeedback(`Mode update failed: ${err.message}`, feedbackKindForError(err));
  }
}

function beginGaCalc() {
  state.gaCalcCount += 1;
  renderGaNnHud(state.dashboard);
}

function endGaCalc() {
  state.gaCalcCount = Math.max(0, state.gaCalcCount - 1);
  renderGaNnHud(state.dashboard);
}

function wireEvents() {
  // Sidebar route navigation -> single screen view swap
  document.querySelectorAll("[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => setActivePage(btn.dataset.page));
  });
  document.querySelectorAll("[data-goto-page]").forEach((btn) => {
    btn.addEventListener("click", () => setActivePage(btn.dataset.gotoPage));
  });
  el("hud-bf-pill")?.addEventListener("click", () => setActivePage("buildings"));

  el("btn-gann-recalc")?.addEventListener("click", async () => {
    beginGaCalc();
    try {
      showFeedback("Running GA + Neural Network sequence optimizer...", "ok");
      const res = await apiCommand({ command: "plan", planner_mode: "ga_nn", refresh: false });
      state.dashboardSeq += 1;
      if (res.optimizer_summary) {
        state.lastOptimizerSummary = res.optimizer_summary;
      }
      if (state.dashboard) {
        state.dashboard = {
          ...state.dashboard,
          plan: Array.isArray(res.actions) ? res.actions : state.dashboard.plan,
          optimizer_summary: res.optimizer_summary ?? state.dashboard.optimizer_summary,
        };
        renderPlanTable(state.dashboard);
      } else {
        await loadDashboard({ refresh: false, preserveConfig: true, force: true });
      }
      showFeedback(
        `GA+NN evaluation complete (${(res.actions || []).length} planned actions).`,
        "ok"
      );
    } catch (err) {
      showFeedback(`GA+NN calculation failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      endGaCalc();
    }
  });

  el("btn-sync").addEventListener("click", async () => {
    const isGa = String(state.dashboard?.config?.planner_mode || "").toLowerCase() === "ga_nn";
    if (isGa) {
      beginGaCalc();
    }
    try {
      showFeedback("Syncing live snapshot from Chrome...", "ok");
      const ok = await loadDashboard({ refresh: true, preserveConfig: true, force: true });
      if (ok) {
        if (state.dashboard?.health?.rate_limited) {
          const rem = Math.ceil(finiteNum(state.dashboard.health.retry_after, 0));
          showFeedback(
            `Synced cached state — SpaceInvasion rate-limit cooldown active (${rem > 0 ? `${rem}s` : "wait"}).`,
            "warn"
          );
        } else {
          showFeedback("Synced with live Chrome session.", "ok");
        }
      }
    } finally {
      if (isGa) {
        endGaCalc();
      }
    }
  });

  el("btn-ensure-session").addEventListener("click", async () => {
    try {
      showFeedback("Attaching Chrome to SpaceInvasion universe...", "ok");
      const res = await apiCommand({ command: "ensure_session" });
      showFeedback(res.message || "Entered universe.", "ok");
      await loadDashboard({ refresh: true, preserveConfig: true, force: true });
    } catch (err) {
      showFeedback(`Enter universe failed: ${err.message}`, feedbackKindForError(err));
    }
  });

  el("btn-tick-now").addEventListener("click", async () => {
    const isGa = String(state.dashboard?.config?.planner_mode || "").toLowerCase() === "ga_nn";
    if (isGa) {
      beginGaCalc();
    }
    try {
      const dryRun = (state.modeOverride || "auto") === "dry-run";
      showFeedback(`Running 1 ${dryRun ? "dry-run" : "live"} tick...`, "ok");
      const res = await apiCommand({ command: "tick_once", dry_run: dryRun });
      showFeedback(res.message || "Tick finished.", "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err) {
      showFeedback(`Tick failed: ${err.message}`, feedbackKindForError(err));
    } finally {
      if (isGa) {
        endGaCalc();
      }
    }
  });

  el("btn-pause-toggle").addEventListener("click", async () => {
    const currentPaused = Boolean(state.dashboard?.bot_control?.paused);
    try {
      await apiCommand({ command: "bot_control", paused: !currentPaused });
      await loadDashboard({ refresh: false, force: true });
      showFeedback(!currentPaused ? "Commander auto-loop paused." : "Commander auto-loop resumed.", "ok");
    } catch (err) {
      showFeedback(`Pause update failed: ${err.message}`, feedbackKindForError(err));
    }
  });

  document.querySelectorAll("[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => updateLoopMode(btn.dataset.mode));
  });

  el("planet-select").addEventListener("change", (e) => {
    state.selectedPlanetId = Number(e.target.value);
    state.simPopulatedOnce = false;
    savePersistedUiState();
    onPlanetChanged();
  });

  el("btn-claim-tutorial").addEventListener("click", async () => {
    try {
      const res = await apiCommand({ command: "claim_tutorial", dry_run: false });
      showFeedback(res.message || "Tutorial claim submitted.", "ok");
      await loadDashboard({ refresh: true, force: true });
    } catch (err) {
      showFeedback(`Tutorial claim failed: ${err.message}`, feedbackKindForError(err));
    }
  });

  // Blast furnace quick toggles & internalEconomy rate sync
  el("btn-dash-b2-toggle")?.addEventListener("click", (e) => {
    const pct = Number(e.currentTarget.dataset.targetPct ?? 0);
    toggleBlastFurnaceRate(pct);
  });
  el("btn-b2-pause")?.addEventListener("click", () => toggleBlastFurnaceRate(0));
  el("btn-b2-resume")?.addEventListener("click", () => toggleBlastFurnaceRate(100));
  el("btn-sync-economy")?.addEventListener("click", async () => {
    try {
      await apiCommand({ command: "navigate", action: "internalEconomy", return_home: true });
      showFeedback("Synced live internalEconomy production percentages.", "ok");
      await loadDashboard({ refresh: true, force: true });
      await refreshBuildingsEtaForSelectedPlanet(false);
    } catch (err) {
      showFeedback(`Sync failed: ${err.message}`, feedbackKindForError(err));
    }
  });

  // Buildings & ETAs filter tabs
  document.querySelectorAll("[data-bld-cat]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.bldCategory = btn.dataset.bldCat;
      savePersistedUiState();
      if (state.dashboard) renderBuildingsView(state.dashboard);
    });
  });

  // Combat & Fleet sub-tabs & simulator
  document.querySelectorAll("[data-cmb-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.combatSubTab = btn.dataset.cmbTab;
      savePersistedUiState();
      document.querySelectorAll("[data-cmb-tab]").forEach((b) => b.classList.toggle("active", b === btn));
      el("cmb-pane-ships").hidden = state.combatSubTab !== "ships";
      el("cmb-pane-defense").hidden = state.combatSubTab !== "defense";
      el("cmb-pane-targets").hidden = state.combatSubTab !== "targets";
      el("cmb-pane-escape").hidden = state.combatSubTab !== "escape";
    });
  });

  el("btn-scan-galaxy")?.addEventListener("click", async () => {
    const planet = activePlanetFrom(state.dashboard);
    try {
      showFeedback("Scanning home Galaxy system in Chrome...", "ok");
      const res = await apiCommand({
        command: "scan_galaxy",
        planet_id: planet?.planet_id,
        dry_run: false,
      });
      state.combatSubTab = "targets";
      savePersistedUiState();
      applySavedTabButtons();
      showFeedback(res.message || "Galaxy scan completed.", "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err) {
      showFeedback(`Galaxy scan failed: ${err.message}`, feedbackKindForError(err));
    }
  });

  el("btn-refresh-combat")?.addEventListener("click", async () => {
    showFeedback("Refreshing live Shipyard offers from Chrome...", "ok");
    const ok = await refreshCombatForSelectedPlanet({ refresh: true });
    if (ok) {
      showFeedback("Shipyard & Combat state refreshed.", "ok");
    }
  });

  el("btn-sim-use-owned")?.addEventListener("click", () => {
    populateSimWithOwnedFleet(currentCombat(state.dashboard));
    runCombatSimulation();
  });
  el("btn-run-sim")?.addEventListener("click", runCombatSimulation);

  el("btn-calc-fleetsave").addEventListener("click", async () => {
    const planet = activePlanetFrom(state.dashboard);
    const target = el("fs-target").value.trim();
    try {
      const res = await apiCommand({
        command: "fleetsave_plan",
        planet_id: planet?.planet_id,
        target_coords: target || undefined,
      });
      el("fleetsave-output").textContent = JSON.stringify(res.escape_plan, null, 2);
    } catch (err) {
      el("fleetsave-output").textContent = `Error: ${err.message}`;
    }
  });

  document.querySelectorAll(".nav-jump").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      try {
        await apiCommand({ command: "navigate", action });
        showFeedback(`Chrome switched to ${action}.`, "ok");
      } catch (err) {
        showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
      }
    });
  });

  document.querySelectorAll("[data-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.queueFilter = chip.dataset.filter;
      savePersistedUiState();
      document.querySelectorAll("[data-filter]").forEach((c) => c.classList.toggle("active", c === chip));
      if (state.dashboard) renderPlanTable(state.dashboard);
    });
  });

  el("btn-dash-add-queue")?.addEventListener("click", () => {
    setActivePage("operations");
    if (el("mq-kind")) el("mq-kind").value = "BUILD";
    syncManualQueueComposer(state.dashboard, { forceDefaultLevel: true });
    el("mq-kind")?.focus?.();
  });

  el("btn-dash-add-fleet-queue")?.addEventListener("click", () => {
    setActivePage("operations");
    if (el("mq-kind")) el("mq-kind").value = "BUILD_SHIP";
    syncManualQueueComposer(state.dashboard, { forceDefaultLevel: true });
    el("mq-kind")?.focus?.();
  });

  el("btn-dash-add-res-queue")?.addEventListener("click", () => {
    setActivePage("operations");
    if (el("mq-kind")) el("mq-kind").value = "RESEARCH";
    syncManualQueueComposer(state.dashboard, { forceDefaultLevel: true });
    el("mq-kind")?.focus?.();
  });

  el("btn-dash-goto-research")?.addEventListener("click", () => {
    state.bldCategory = "research";
    savePersistedUiState();
    applySavedTabButtons();
    setActivePage("buildings");
    if (state.dashboard) renderBuildingsView(state.dashboard);
  });

  el("mq-kind")?.addEventListener("change", () => {
    syncManualQueueComposer(state.dashboard, { forceDefaultLevel: true });
  });
  el("mq-planet")?.addEventListener("change", () => {
    syncManualQueueComposer(state.dashboard, { forceDefaultLevel: true });
  });
  el("mq-target-id")?.addEventListener("change", () => {
    syncManualQueueComposer(state.dashboard, { forceDefaultLevel: true });
  });
  el("btn-mq-clear")?.addEventListener("click", () => {
    clearAllManualQueue();
  });

  el("mq-composer-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const kind = String(el("mq-kind")?.value || "BUILD").toUpperCase();
    const priority = String(el("mq-priority")?.value || "P1").toUpperCase();
    const position = String(el("mq-position")?.value || "top").toLowerCase();
    const planetId = Math.max(0, Math.round(finiteNum(el("mq-planet")?.value, 0)));
    const note = String(el("mq-note")?.value || "").trim();
    const payload = {
      kind,
      priority,
      position,
      planet_id: planetId,
      note,
    };

    if (["BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING"].includes(kind)) {
      payload.target_id = Math.max(1, Math.round(finiteNum(el("mq-target-id")?.value, 1)));
      const rawLvl = String(el("mq-level")?.value ?? "").trim();
      if (rawLvl !== "") {
        payload.target_level = Math.max(0, Math.round(finiteNum(rawLvl, 1)));
      }
    } else if (["SPY", "ATTACK", "RECYCLE", "COLONIZE", "SCAN_GALAXY"].includes(kind)) {
      const coords = String(el("mq-coords")?.value || "").trim();
      if (!coords) {
        showFeedback("Enter target coordinates (e.g. 02:381:12) before queuing.", "warn");
        el("mq-coords")?.focus?.();
        return;
      }
      payload.target_coords = coords;
      if (kind === "SPY" || kind === "RECYCLE") {
        payload.target_level = Math.max(1, Math.round(finiteNum(el("mq-level")?.value, 1)));
      }
    }

    const res = await enqueueManualCommand(payload);
    if (res && el("mq-note")) {
      el("mq-note").value = "";
    }
  });

  document.querySelectorAll("[data-log]").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      state.activeLogSource = tabBtn.dataset.log;
      savePersistedUiState();
      document.querySelectorAll("[data-log]").forEach((b) => b.classList.toggle("active", b === tabBtn));
      if (state.dashboard) renderLogs(state.dashboard.logs, state.dashboard.db_summary);
    });
  });

  el("btn-refresh-logs").addEventListener("click", () => loadDashboard({ refresh: false, force: true }));

  // Track config edits (both input & change) so background polling doesn't stomp unsaved form inputs
  document.querySelectorAll("#view-config input, #view-config select").forEach((inp) => {
    const markDirty = () => {
      state.configDirty = true;
    };
    inp.addEventListener("change", markDirty);
    inp.addEventListener("input", markDirty);
  });

  el("btn-save-config").addEventListener("click", async () => {
    const plannerModeVal = el("cfg-planner_mode") ? el("cfg-planner_mode").value : "priority";
    const payload = {
      planner_mode: plannerModeVal,
      playstyle: el("cfg-playstyle").value,
      auto_throttle_blast_furnace: el("cfg-auto_throttle_blast_furnace").checked,
      allow_research: el("cfg-allow_research").checked,
      allow_shipyard: el("cfg-allow_shipyard").checked,
      allow_multi_planet: el("cfg-allow_multi_planet").checked,
      merchant_mode: el("cfg-merchant_mode").checked,
      allow_fleetsave: el("cfg-allow_fleetsave").checked,
      allow_galaxy_scan: el("cfg-allow_galaxy_scan").checked,
      allow_raids: el("cfg-allow_raids").checked,
      allow_pvp_in_az: el("cfg-allow_pvp_in_az").checked,
      allow_colonize: el("cfg-allow_colonize").checked,
      allow_transmitter: Boolean(el("cfg-allow_transmitter")?.checked ?? true),
      forbid_planetary_solar: Boolean(el("cfg-forbid_planetary_solar")?.checked),
    };
    for (const numField of [
      "storage_warn_ratio",
      "combat_safety_margin",
      "fleet_save_fuel_reserve_spice",
      "action_batch_interval_seconds",
      "min_colony_fields",
      "max_planets",
      "galaxy_sweep_pages",
      "galaxy_sweep_interval_seconds",
      "max_actions_per_hour",
      "ga_population_size",
      "ga_generations",
      "ga_horizon_steps",
    ]) {
      const inp = el(`cfg-${numField}`);
      const raw = inp ? String(inp.value ?? "").trim() : "";
      if (raw !== "") {
        const parsed = finiteNum(raw, null);
        payload[numField] = parsed !== null ? parsed : raw;
      }
    }
    const seedInp = el("cfg-ga_seed");
    if (seedInp) {
      const rawSeed = String(seedInp.value ?? "").trim();
      if (rawSeed === "") {
        payload.ga_seed = null;
      } else if (/^-?\d+$/.test(rawSeed)) {
        const parsedSeed = Number(rawSeed);
        payload.ga_seed = Number.isSafeInteger(parsedSeed) ? parsedSeed : rawSeed;
      } else {
        payload.ga_seed = rawSeed;
      }
    }

    const isGa = String(plannerModeVal).toLowerCase() === "ga_nn";
    if (isGa) {
      beginGaCalc();
    }
    state.dashboardSeq += 1;
    state.configSaving = (state.configSaving || 0) + 1;
    try {
      const res = await apiCommand({ command: "update_config", config: payload });
      state.dashboardSeq += 1;
      state.configDirty = false;
      if (res.config && typeof res.config === "object") {
        if (state.dashboard) {
          state.dashboard = {
            ...state.dashboard,
            config: res.config,
            planner_mode: res.config.planner_mode || state.dashboard.planner_mode,
          };
        } else {
          state.dashboard = {
            config: res.config,
            planner_mode: res.config.planner_mode || "priority",
          };
        }
        renderGaNnHud(state.dashboard);
      }
      renderConfigForm(res.config);
      if (!state.agSimDirty) {
        populateAgParamControls(res.config);
      }
      showFeedback("Saved config.yaml — Commander & API updated.", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err) {
      showFeedback(`Failed saving config: ${err.message}`, feedbackKindForError(err));
    } finally {
      state.configSaving = Math.max(0, (state.configSaving || 0) - 1);
      if (isGa) {
        endGaCalc();
      }
    }
  });

  if (el("btn-claim-transmitter")) {
    el("btn-claim-transmitter").addEventListener("click", async () => {
      try {
        showFeedback("Checking daily free Interdimensional Transmitter spin...", "info");
        const res = await apiCommand({ command: "activate_transmitter", dry_run: false });
        showFeedback(
          res.message || "Interdimensional Transmitter check completed.",
          res.executed ? "ok" : "info"
        );
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Transmitter check failed: ${err.message}`, feedbackKindForError(err));
      }
    });
  }

  if (el("btn-galaxy-sweep")) {
    el("btn-galaxy-sweep").addEventListener("click", async () => {
      const pagesVal = finiteNum(el("cfg-galaxy_sweep_pages")?.value, 100) || 100;
      try {
        showFeedback(`Starting ${pagesVal}-page randomized galaxy sweep...`, "info");
        const res = await apiCommand(
          { command: "galaxy_sweep", pages: pagesVal, dry_run: false },
          { timeoutMs: 300000 }
        );
        showFeedback(res.message || "Galaxy sweep finished.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Galaxy sweep failed: ${err.message}`, feedbackKindForError(err));
      }
    });
  }

  // Action Graph mode switch, edge filter, right-side parameter simulation, reset & explicit apply
  document.querySelectorAll("[data-ag-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetMode = btn.dataset.agMode === "ga_nn" ? "ga_nn" : "priority";
      const modeSel = el("ag-cfg-planner_mode");
      if (modeSel) {
        modeSel.value = targetMode;
      }
      syncAgModeSwitchButtons(targetMode);
      runAgSimulation({ explicit: false });
    });
  });

  document.querySelectorAll("[data-ag-edge-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.agEdgeFilter = chip.dataset.agEdgeFilter === "deps" ? "deps" : "all";
      savePersistedUiState();
      document.querySelectorAll("[data-ag-edge-filter]").forEach((c) => {
        c.classList.toggle("active", c === chip);
      });
      if (state.actionGraphData) {
        renderActionGraphView(state.actionGraphData);
      }
    });
  });

  document.querySelectorAll("[data-ag-ptab]").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      state.agParamTab = tabBtn.dataset.agPtab === "scope" ? "scope" : "core";
      savePersistedUiState();
      document.querySelectorAll("[data-ag-ptab]").forEach((b) => {
        const isAct = b === tabBtn;
        b.classList.toggle("active", isAct);
        b.setAttribute("aria-selected", isAct ? "true" : "false");
      });
      if (el("ag-param-pane-core")) el("ag-param-pane-core").hidden = state.agParamTab !== "core";
      if (el("ag-param-pane-scope")) el("ag-param-pane-scope").hidden = state.agParamTab !== "scope";
    });
  });

  document.querySelectorAll("#ag-control-panel input, #ag-control-panel select").forEach((inp) => {
    inp.addEventListener("change", () => {
      if (inp.id === "ag-cfg-planner_mode") {
        syncAgModeSwitchButtons(inp.value);
      }
      runAgSimulation({ explicit: false });
    });
    if (inp.tagName === "INPUT" && inp.type !== "checkbox") {
      inp.addEventListener("input", () => {
        state.agSimSeq += 1;
        setAgDirtyState(true);
        if (state.agDebounceTimer) {
          clearTimeout(state.agDebounceTimer);
        }
        state.agDebounceTimer = setTimeout(() => {
          state.agDebounceTimer = null;
          runAgSimulation({ explicit: false });
        }, 180);
      });
    }
  });

  el("btn-ag-simulate")?.addEventListener("click", () => {
    runAgSimulation({ explicit: true });
  });

  el("btn-ag-refresh")?.addEventListener("click", async () => {
    if (state.agSimDirty) {
      await runAgSimulation({ explicit: true });
    } else {
      const ok = await refreshActiveActionGraph(false);
      if (ok) {
        showFeedback("Action graph refreshed from empire snapshot.", "ok");
      }
    }
  });

  el("btn-ag-reset")?.addEventListener("click", async () => {
    if (state.agDebounceTimer) {
      clearTimeout(state.agDebounceTimer);
      state.agDebounceTimer = null;
    }
    state.agSimSeq += 1;
    setAgDirtyState(false);
    const activeCfg = state.dashboard?.config || null;
    if (activeCfg) {
      populateAgParamControls(activeCfg);
    }
    if (state.dashboard?.action_graph) {
      state.actionGraphData = state.dashboard.action_graph;
      renderActionGraphView(state.actionGraphData);
    } else if (state.dashboard) {
      state.actionGraphData = buildClientFallbackGraph(state.dashboard);
      renderActionGraphView(state.actionGraphData);
    }
    if (state.apiOnline) {
      await refreshActiveActionGraph(false);
    }
    showFeedback("Reset simulation controls to active StrategyConfig.", "ok");
  });

  el("btn-ag-apply")?.addEventListener("click", async () => {
    if (state.agDebounceTimer) {
      clearTimeout(state.agDebounceTimer);
      state.agDebounceTimer = null;
    }
    const payload = collectAgDraftPayload();
    const isGa = String(payload.planner_mode || "").toLowerCase() === "ga_nn";
    if (isGa) {
      beginGaCalc();
    }
    state.agSimSeq += 1;
    state.dashboardSeq += 1;
    state.configSaving = (state.configSaving || 0) + 1;
    try {
      const res = await apiCommand({
        command: "update_config",
        config: payload,
        include_action_graph: true,
      });
      state.dashboardSeq += 1;
      state.configDirty = false;
      setAgDirtyState(false);
      if (res.config && typeof res.config === "object") {
        populateAgParamControls(res.config);
        renderConfigForm(res.config);
        if (state.dashboard) {
          state.dashboard = {
            ...state.dashboard,
            config: res.config,
            planner_mode: res.config.planner_mode || state.dashboard.planner_mode,
            action_graph: res.action_graph || state.dashboard.action_graph,
          };
        } else {
          state.dashboard = {
            config: res.config,
            planner_mode: res.config.planner_mode || "priority",
            action_graph: res.action_graph || null,
          };
        }
        renderGaNnHud(state.dashboard);
      }
      if (res.action_graph && typeof res.action_graph === "object") {
        state.actionGraphData = res.action_graph;
        renderActionGraphView(res.action_graph);
      }
      showFeedback("Applied parameters to active StrategyConfig (config.yaml saved).", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err) {
      showFeedback(`Failed applying parameters: ${err.message}`, feedbackKindForError(err));
    } finally {
      state.configSaving = Math.max(0, (state.configSaving || 0) - 1);
      if (isGa) {
        endGaCalc();
      }
    }
  });

  const sidebarToggle = el("btn-sidebar-toggle");
  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", () => {
      const sidebar = el("app-sidebar");
      const isOpen = sidebar.classList.toggle("is-open");
      sidebarToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
  }

  // Espionage, Colonies, Attacks & Recyclers view controls
  document.querySelectorAll("[data-esp-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.espTab = btn.dataset.espTab === "reports" ? "reports" : "targets";
      document.querySelectorAll("[data-esp-tab]").forEach((b) => b.classList.toggle("active", b === btn));
      if (el("esp-pane-targets")) el("esp-pane-targets").hidden = state.espTab !== "targets";
      if (el("esp-pane-reports")) el("esp-pane-reports").hidden = state.espTab !== "reports";
    });
  });
  el("btn-esp-sync")?.addEventListener("click", syncAllInboxFolders);
  el("btn-esp-refresh")?.addEventListener("click", refreshEspionageView);

  document.querySelectorAll("[data-col-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.colonyFilter = chip.dataset.colFilter || "all";
      document.querySelectorAll("[data-col-filter]").forEach((c) => c.classList.toggle("active", c === chip));
      if (state.colonyData) renderColoniesView(state.colonyData);
    });
  });
  el("btn-col-sync")?.addEventListener("click", syncAllInboxFolders);
  el("btn-col-scan")?.addEventListener("click", async () => {
    const planet = activePlanetFrom(state.dashboard);
    try {
      showFeedback("Scanning Galaxy system for free slots...", "ok");
      const res = await apiCommand({
        command: "scan_galaxy",
        planet_id: planet?.planet_id,
        dry_run: false,
      });
      showFeedback(res.message || "Galaxy scan completed.", "ok");
      await loadDashboard({ refresh: false, force: true });
      await refreshColoniesView();
    } catch (err) {
      showFeedback(`Galaxy scan failed: ${err.message}`, feedbackKindForError(err));
    }
  });
  el("btn-col-save")?.addEventListener("click", async () => {
    const minVal = Math.max(50, Math.min(600, Math.round(finiteNum(el("col-min-fields")?.value, 300))));
    const allowCol = Boolean(el("col-allow-colonize")?.checked);
    try {
      const res = await apiCommand({
        command: "update_config",
        config: { min_colony_fields: minVal, allow_colonize: allowCol },
      });
      if (res.config) {
        renderConfigForm(res.config);
        if (!state.agSimDirty) populateAgParamControls(res.config);
      }
      showFeedback(`Colony policy saved: min ${minVal} fields, auto-colonize=${allowCol}.`, "ok");
      await loadDashboard({ refresh: false, force: true });
      await refreshColoniesView();
    } catch (err) {
      showFeedback(`Failed updating colony policy: ${err.message}`, feedbackKindForError(err));
    }
  });

  document.querySelectorAll("[data-atk-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.atkTab = btn.dataset.atkTab === "blocks" ? "blocks" : "battles";
      document.querySelectorAll("[data-atk-tab]").forEach((b) => b.classList.toggle("active", b === btn));
      if (el("atk-pane-battles")) el("atk-pane-battles").hidden = state.atkTab !== "battles";
      if (el("atk-pane-blocks")) el("atk-pane-blocks").hidden = state.atkTab !== "blocks";
    });
  });
  el("btn-atk-sync")?.addEventListener("click", syncAllInboxFolders);
  el("btn-atk-refresh")?.addEventListener("click", refreshAttacksView);

  document.querySelectorAll("[data-rec-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.dataset.recTab;
      state.recTab = t === "debris" || t === "emails" ? t : "harvests";
      document.querySelectorAll("[data-rec-tab]").forEach((b) => b.classList.toggle("active", b === btn));
      if (el("rec-pane-harvests")) el("rec-pane-harvests").hidden = state.recTab !== "harvests";
      if (el("rec-pane-debris")) el("rec-pane-debris").hidden = state.recTab !== "debris";
      if (el("rec-pane-emails")) el("rec-pane-emails").hidden = state.recTab !== "emails";
    });
  });
  document.querySelectorAll("[data-email-cat]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.emailCatFilter = chip.dataset.emailCat || "all";
      document.querySelectorAll("[data-email-cat]").forEach((c) => c.classList.toggle("active", c === chip));
      refreshRecyclersView();
    });
  });
  el("btn-rec-sync")?.addEventListener("click", syncAllInboxFolders);

  el("btn-notifications")?.addEventListener("click", () => {
    openNotificationCenter();
  });
  el("btn-notif-center-close")?.addEventListener("click", () => {
    closeNotificationCenter();
  });
  el("btn-notif-mark-all")?.addEventListener("click", async () => {
    try {
      await apiCommand({ command: "notifications_ack_all", status: "read" });
      await refreshNotificationCenter();
    } catch (err) {
      showFeedback(`Failed marking notifications read: ${err.message}`, feedbackKindForError(err));
    }
  });
  document.querySelectorAll("[data-notif-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const f = btn.dataset.notifFilter;
      state.notifFilter = f === "unread" || f === "all" ? f : "active";
      document.querySelectorAll("[data-notif-filter]").forEach((b) => b.classList.toggle("active", b === btn));
      refreshNotificationCenter();
    });
  });
  el("btn-notif-help-ack")?.addEventListener("click", () => ackActiveHelpPopup("read"));
  el("btn-notif-help-dismiss")?.addEventListener("click", () => ackActiveHelpPopup("dismissed"));
  el("btn-notif-help-all")?.addEventListener("click", () => {
    closeHelpPopupOnly();
    openNotificationCenter();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev && ev.key === "Escape") {
      if (el("notif-center-modal") && !el("notif-center-modal").hidden) {
        closeNotificationCenter();
      } else if (el("notif-help-modal") && !el("notif-help-modal").hidden) {
        closeHelpPopupOnly();
      }
    }
  });
}

function formatNotifAge(tsVal) {
  const ts = finiteNum(tsVal, 0);
  if (ts <= 0) return "just now";
  const sec = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function syncNotificationsPayload(notifPayload) {
  const items = Array.isArray(notifPayload?.items) ? notifPayload.items : [];
  const unreadCnt = Math.max(0, Math.round(finiteNum(notifPayload?.unread_count, 0)));
  state.notifications = { unread_count: unreadCnt, items };

  const badge = el("notif-unread-badge");
  const bellBtn = el("btn-notifications");
  if (badge) {
    badge.textContent = unreadCnt > 99 ? "99+" : String(unreadCnt);
    badge.hidden = unreadCnt <= 0;
  }
  if (bellBtn) {
    bellBtn.classList.toggle("has-unread", unreadCnt > 0);
    bellBtn.setAttribute(
      "aria-label",
      unreadCnt > 0 ? `Open system notifications (${unreadCnt} unread)` : "Open system notifications"
    );
  }

  // Auto-open the help popup on the newest unseen unread notification
  const newestUnread = items.find((n) => n && n.status === "unread" && finiteNum(n.id, 0) > 0);
  if (newestUnread) {
    const sig = `${newestUnread.id}:${finiteNum(newestUnread.occurrence_count, 1)}`;
    const centerOpen = el("notif-center-modal") && !el("notif-center-modal").hidden;
    if (!state.seenHelpNotifSigs.has(sig) && !centerOpen) {
      state.seenHelpNotifSigs.add(sig);
      showHelpPopupForNotification(newestUnread);
    }
  } else if (state.activeHelpNotifId !== null) {
    closeHelpPopupOnly();
  }
}

function showHelpPopupForNotification(n) {
  const modal = el("notif-help-modal");
  if (!modal || !n) return;
  state.activeHelpNotifId = finiteNum(n.id, 0);
  const sevEl = el("notif-help-sev");
  if (sevEl) {
    sevEl.textContent = String(n.severity || "warning").toUpperCase();
  }
  if (el("notif-help-title")) {
    el("notif-help-title").textContent = String(n.title || "Action Validation Needs Attention");
  }
  const cnt = Math.max(1, Math.round(finiteNum(n.occurrence_count, 1)));
  const metaParts = [];
  if (n.planet_coords) metaParts.push(`[${n.planet_coords}]`);
  if (n.action_kind) metaParts.push(String(n.action_kind));
  if (cnt > 1) metaParts.push(`x${cnt}`);
  metaParts.push(formatNotifAge(n.updated_ts));
  if (el("notif-help-meta")) {
    el("notif-help-meta").textContent = metaParts.join(" · ");
  }
  if (el("notif-help-message")) {
    el("notif-help-message").textContent = String(n.message || "");
  }
  if (el("notif-help-prompt")) {
    el("notif-help-prompt").textContent = String(
      n.help_prompt || "Please inspect Chrome (:9222) to verify this action or acknowledge to continue."
    );
  }
  modal.hidden = false;
}

function closeHelpPopupOnly() {
  const modal = el("notif-help-modal");
  if (modal) modal.hidden = true;
  state.activeHelpNotifId = null;
}

async function ackActiveHelpPopup(targetStatus) {
  const nid = state.activeHelpNotifId;
  closeHelpPopupOnly();
  if (!nid || nid <= 0) return;
  try {
    await apiCommand({ command: "notification_ack", notification_id: nid, status: targetStatus });
  } catch (err) {
    showFeedback(`Failed updating notification: ${err.message}`, feedbackKindForError(err));
  }
}

async function openNotificationCenter() {
  const modal = el("notif-center-modal");
  if (!modal) return;
  modal.hidden = false;
  el("btn-notifications")?.setAttribute("aria-expanded", "true");
  await refreshNotificationCenter();
}

function closeNotificationCenter() {
  const modal = el("notif-center-modal");
  if (modal) modal.hidden = true;
  el("btn-notifications")?.setAttribute("aria-expanded", "false");
}

async function refreshNotificationCenter() {
  const container = el("notif-center-list");
  if (!container) return;
  let items = Array.isArray(state.notifications?.items) ? state.notifications.items : [];
  try {
    const res = await apiCommand({
      command: "notifications_list",
      status: state.notifFilter,
      limit: 60,
    });
    if (Array.isArray(res?.items)) {
      items = res.items;
      syncNotificationsPayload({
        unread_count: res.unread_count,
        items: state.notifFilter === "active" ? res.items : state.notifications.items,
      });
    }
  } catch {
    // Fall back to cached dashboard notifications if offline
  }

  container.textContent = "";
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent =
      state.notifFilter === "unread"
        ? "No unread notifications."
        : "No notifications recorded in SQLite yet.";
    container.appendChild(p);
    return;
  }

  items.forEach((n) => {
    const row = document.createElement("div");
    row.className = `notif-item-row${n.status === "unread" ? " is-unread" : ""}`;

    const head = document.createElement("div");
    head.className = "notif-item-head";

    const titleWrap = document.createElement("div");
    titleWrap.className = "notif-item-title-wrap";

    const pill = document.createElement("span");
    const sev = String(n.severity || "warning").toLowerCase();
    pill.className = `notif-sev-pill tabular ${sev === "error" ? "sev-error" : "sev-warning"}`;
    pill.textContent = sev.toUpperCase();
    titleWrap.appendChild(pill);

    const ttl = document.createElement("span");
    ttl.className = "notif-item-title";
    ttl.textContent = String(n.title || "Notification");
    titleWrap.appendChild(ttl);

    const meta = document.createElement("span");
    meta.className = "notif-item-meta tabular";
    const cnt = Math.max(1, Math.round(finiteNum(n.occurrence_count, 1)));
    const mParts = [];
    if (n.planet_coords) mParts.push(`[${n.planet_coords}]`);
    if (cnt > 1) mParts.push(`x${cnt}`);
    mParts.push(String(n.status || "unread").toUpperCase());
    mParts.push(formatNotifAge(n.updated_ts));
    meta.textContent = mParts.join(" · ");

    head.appendChild(titleWrap);
    head.appendChild(meta);
    row.appendChild(head);

    if (n.message) {
      const msgEl = document.createElement("p");
      msgEl.className = "notif-item-msg tabular";
      msgEl.textContent = String(n.message);
      row.appendChild(msgEl);
    }
    if (n.help_prompt) {
      const helpEl = document.createElement("p");
      helpEl.className = "notif-item-help";
      helpEl.textContent = String(n.help_prompt);
      row.appendChild(helpEl);
    }

    const actions = document.createElement("div");
    actions.className = "notif-item-actions";
    if (n.status === "unread") {
      const btnRead = document.createElement("button");
      btnRead.type = "button";
      btnRead.className = "btn btn-secondary btn-sm";
      btnRead.textContent = "Mark Read";
      btnRead.addEventListener("click", async () => {
        await apiCommand({ command: "notification_ack", notification_id: n.id, status: "read" });
        await refreshNotificationCenter();
      });
      actions.appendChild(btnRead);
    }
    if (n.status !== "dismissed") {
      const btnDis = document.createElement("button");
      btnDis.type = "button";
      btnDis.className = "btn btn-secondary btn-sm";
      btnDis.textContent = "Dismiss";
      btnDis.addEventListener("click", async () => {
        await apiCommand({ command: "notification_ack", notification_id: n.id, status: "dismissed" });
        await refreshNotificationCenter();
      });
      actions.appendChild(btnDis);
    }
    if (actions.children.length > 0) {
      row.appendChild(actions);
    }
    container.appendChild(row);
  });
}

function applySavedTabButtons() {
  document.querySelectorAll("[data-cmb-tab]").forEach((b) => b.classList.toggle("active", b.dataset.cmbTab === state.combatSubTab));
  el("cmb-pane-ships").hidden = state.combatSubTab !== "ships";
  el("cmb-pane-defense").hidden = state.combatSubTab !== "defense";
  el("cmb-pane-targets").hidden = state.combatSubTab !== "targets";
  el("cmb-pane-escape").hidden = state.combatSubTab !== "escape";

  document.querySelectorAll("[data-filter]").forEach((c) => c.classList.toggle("active", c.dataset.filter === state.queueFilter));
  document.querySelectorAll("[data-log]").forEach((b) => b.classList.toggle("active", b.dataset.log === state.activeLogSource));
  document.querySelectorAll("[data-ag-edge-filter]").forEach((c) => {
    c.classList.toggle("active", c.dataset.agEdgeFilter === state.agEdgeFilter);
  });
  document.querySelectorAll("[data-ag-ptab]").forEach((b) => {
    const isAct = b.dataset.agPtab === state.agParamTab;
    b.classList.toggle("active", isAct);
    b.setAttribute("aria-selected", isAct ? "true" : "false");
  });
  if (el("ag-param-pane-core")) el("ag-param-pane-core").hidden = state.agParamTab !== "core";
  if (el("ag-param-pane-scope")) el("ag-param-pane-scope").hidden = state.agParamTab !== "scope";

  document.querySelectorAll("[data-esp-tab]").forEach((b) => b.classList.toggle("active", b.dataset.espTab === state.espTab));
  if (el("esp-pane-targets")) el("esp-pane-targets").hidden = state.espTab !== "targets";
  if (el("esp-pane-reports")) el("esp-pane-reports").hidden = state.espTab !== "reports";

  document.querySelectorAll("[data-col-filter]").forEach((c) => c.classList.toggle("active", c.dataset.colFilter === state.colonyFilter));

  document.querySelectorAll("[data-atk-tab]").forEach((b) => b.classList.toggle("active", b.dataset.atkTab === state.atkTab));
  if (el("atk-pane-battles")) el("atk-pane-battles").hidden = state.atkTab !== "battles";
  if (el("atk-pane-blocks")) el("atk-pane-blocks").hidden = state.atkTab !== "blocks";

  document.querySelectorAll("[data-rec-tab]").forEach((b) => b.classList.toggle("active", b.dataset.recTab === state.recTab));
  if (el("rec-pane-harvests")) el("rec-pane-harvests").hidden = state.recTab !== "harvests";
  if (el("rec-pane-debris")) el("rec-pane-debris").hidden = state.recTab !== "debris";
  if (el("rec-pane-emails")) el("rec-pane-emails").hidden = state.recTab !== "emails";
}

restorePersistedUiState();
wireEvents();
applySavedTabButtons();
const rawInitialHash = window.location.hash.replace(/^#/, "");
const initialHash = rawInitialHash === "graph" ? "action-graph" : rawInitialHash;
setActivePage(PAGE_TITLES[initialHash] ? initialHash : state.activePage);
checkApiHealthAndHotReload();
loadDashboard({ refresh: false, preserveConfig: false });

// Live 1s countdown ticker for currently executing queue items
setInterval(() => {
  if (document.visibilityState === "visible") {
    tickLiveQueueCountdowns();
  }
}, 1000);

// Fast auto-reconnect + live UI asset hot-reload watcher (1.2s)
setInterval(() => {
  checkApiHealthAndHotReload();
}, 1200);

// Live dashboard telemetry refresh when visible & connected (5s)
setInterval(() => {
  if (document.visibilityState === "visible" && state.apiOnline) {
    loadDashboard({ refresh: false, preserveConfig: true });
  }
}, 5000);

window.addEventListener("online", () => {
  checkApiHealthAndHotReload();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    checkApiHealthAndHotReload();
    loadDashboard({ refresh: false, preserveConfig: true });
  }
});
