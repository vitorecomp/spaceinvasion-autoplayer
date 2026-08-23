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
  gaCalcRunning: false,
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
    if (saved.selectedPlanetId != null) state.selectedPlanetId = Number(saved.selectedPlanetId);
    if (saved.bldCategory) state.bldCategory = String(saved.bldCategory);
    if (saved.selectedUnitKey) state.selectedUnitKey = String(saved.selectedUnitKey);
    if (saved.combatSubTab) state.combatSubTab = String(saved.combatSubTab);
    if (saved.queueFilter) state.queueFilter = String(saved.queueFilter);
    if (saved.activeLogSource) state.activeLogSource = String(saved.activeLogSource);
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
  operations: "OPERATIONS // PRIORITY QUEUE",
  ranking: "RANKING // EVOLUTION & AZ",
  config: "STRATEGY // CONFIG BUCKETS",
  logs: "TERMINAL // LIVE LOGS",
};

const el = (id) => document.getElementById(id);

function fmtInt(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function fmtRate(n) {
  const v = Math.round(Number(n || 0));
  const sign = v > 0 ? "+" : "";
  return `${sign}${fmtInt(v)}/h`;
}

function fmtClock(tsSec) {
  if (!tsSec) return "SYNC --:--:--";
  const d = new Date(Number(tsSec) * 1000);
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

async function apiGet(url, timeoutMs = 6000) {
  const res = await fetchWithTimeout(url, {}, timeoutMs);
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.rateLimited = Boolean(data.rate_limited || res.status === 429);
    err.retryAfter = Number(data.retry_after || 0);
    throw err;
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
  const data = await res.json();
  setApiStatus(true);
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.rateLimited = Boolean(data.rate_limited || res.status === 429);
    err.retryAfter = Number(data.retry_after || 0);
    throw err;
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
    const cdSec = Math.ceil(Number(health.retry_after || 0));
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
  const cap = Number(storage || 0);
  const val = Number(amount || 0);
  const pct = cap > 0 ? Math.min(100, Math.round((val / cap) * 100)) : 0;
  el(`val-${key}`).textContent = fmtInt(val);
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
      rateNode.textContent = fmtRate(hourlyRate);
      rateNode.className = `track-rate${Number(hourlyRate) < 0 ? " is-neg" : ""}`;
    }
  }
}

function getSelectedBlastFurnacePct(dash) {
  const planet = activePlanetFrom(dash);
  const eta = currentBuildingsEta(dash);
  const pct =
    planet?.economy_rates?.resource2 ??
    eta?.planet_economy?.blast_furnace_pct ??
    100;
  return Number(pct);
}

function renderTelemetry(dash) {
  const planet = activePlanetFrom(dash);
  const eta = currentBuildingsEta(dash);
  const pEcon = eta?.planet_economy || null;

  if (planet) {
    el("command-subpath").textContent = `SECTOR // [${planet.coords}] ${planet.name.toUpperCase()}`;
    renderResourceBar("pig", planet.resources.pig_iron, planet.storage.pig_iron, pEcon?.pig_iron_per_hr);
    renderResourceBar("metal", planet.resources.metal, planet.storage.metal, pEcon?.metal_per_hr);
    renderResourceBar("kryp", planet.resources.kryptonite, planet.storage.kryptonite, pEcon?.kryptonite_per_hr);
    renderResourceBar("spice", planet.resources.spice, planet.storage.spice, pEcon?.spice_per_hr);

    const produced = Number(planet.energy?.produced || 0);
    const used = Number(planet.energy?.used || 0);
    const net = produced - used;
    el("val-energy").textContent = `${net >= 0 ? "+" : ""}${fmtInt(net)}`;
    el("energy-ratio").textContent = `${fmtInt(produced)} / ${fmtInt(used)} E`;
    el("planet-specs").textContent = `TEMP ${planet.temperature}°C · FIELDS ${planet.fields_used}/${planet.fields_max}`;
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
  el("dash-rank-val").textContent = cur.server_rank != null ? `#${fmtInt(cur.server_rank)}` : az.label || "#—";
  el("dash-az-val").textContent = `${az.label || "AZ —"} · ${fmtInt(Math.round(cur.baseline_points || 0))} pts`;
  el("dash-pts-hr").textContent = `+${Number(rates.pts_per_hr_steady || 0).toFixed(2)}/h`;
  el("dash-spice-hr").textContent = `${fmtInt(Math.round(rates.spice_eq_per_hr || 0))} SpiceEq/h`;

  const health = dash.health || {};
  el("val-budget").textContent = `${health.remaining_hourly_actions ?? "—"} OPS/HR`;
  el("val-speedup").textContent = dash.state?.speedup_factor ? `SPEED x${dash.state.speedup_factor}` : "STD x1";

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
      tdCoords.textContent = p.coords;

      const tdName = document.createElement("td");
      tdName.textContent = p.name;

      const tdStat = document.createElement("td");
      const badge = document.createElement("span");
      const nearFull =
        p.storage &&
        ((p.storage.pig_iron && p.resources.pig_iron / p.storage.pig_iron >= 0.85) ||
          (p.storage.spice && p.resources.spice / p.storage.spice >= 0.85));
      badge.className = `status-word${nearFull ? " is-warn" : ""}`;
      badge.textContent = nearFull ? "NEAR-CAP" : p.planet_id === chosenId ? "ONLINE" : "READY";
      tdStat.appendChild(badge);

      const tdFields = document.createElement("td");
      tdFields.className = "col-right tabular";
      tdFields.textContent = `${p.fields_used}/${p.fields_max}`;

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
    qNode.textContent = queues
      .map((q) => `[${String(q.category || "JOB").toUpperCase()}] ${q.name} → L${q.target_level ?? "?"}`)
      .join(" · ");
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

function actionPayloadFor(action, dryRun) {
  if (action.kind === "CLAIM_TUTORIAL") return { command: "claim_tutorial", dry_run: dryRun };
  if (action.kind === "BUILD" && action.target_id != null) {
    return { command: "build", building_id: action.target_id, planet_id: action.planet_id, dry_run: dryRun };
  }
  if (action.kind === "RESEARCH" && action.target_id != null) {
    return { command: "research", research_id: action.target_id, planet_id: action.planet_id, dry_run: dryRun };
  }
  if (action.kind === "BUILD_SHIP" && action.target_id != null) {
    return {
      command: "build_ship",
      ship_id: action.target_id,
      quantity: Math.max(1, Number(action.target_level || 1)),
      planet_id: action.planet_id,
      dry_run: dryRun,
    };
  }
  if (action.kind === "THROTTLE_BUILDING" && action.target_id != null) {
    return {
      command: "throttle_building",
      building_id: action.target_id,
      percentage: action.target_level == null ? 0 : Number(action.target_level),
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

  const tdPrio = document.createElement("td");
  const prioTag = document.createElement("span");
  const prioVal = String(act.priority || "?");
  prioTag.className = `prio-tag prio-${prioVal}`;
  const prioDot = document.createElement("span");
  prioDot.className = "prio-dot";
  prioTag.append(prioDot, document.createTextNode(prioVal));
  tdPrio.appendChild(prioTag);
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
  if (!compact && durText && durText !== totalEta) {
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
  if (cmdTemplate && act.executable_now) {
    const wrap = document.createElement("div");
    wrap.className = "row-actions";

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

    wrap.append(runBtn, dryBtn);
    tdOps.appendChild(wrap);
  } else {
    const waitTag = document.createElement("span");
    waitTag.className = "action-sub";
    waitTag.textContent = "Waiting";
    tdOps.appendChild(waitTag);
  }
  tr.appendChild(tdOps);
  return tr;
}

function renderPlanTable(dash) {
  const tbody = el("plan-tbody");
  const allActions = dash.plan || [];
  let actions = allActions;
  if (state.queueFilter === "ready") {
    actions = allActions.filter((a) => a.executable_now);
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

  const dashBody = el("dash-plan-tbody");
  if (dashBody) {
    const topDash = allActions.slice(0, 10);
    if (topDash.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "empty-cell";
      td.textContent = "No planned actions right now.";
      tr.appendChild(td);
      dashBody.replaceChildren(tr);
    } else {
      dashBody.replaceChildren(...topDash.map((a) => buildPlanRow(a, { compact: true })));
    }
  }
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
      const ra = a.reach_hours == null ? 1e9 : Number(a.reach_hours);
      const rb = b.reach_hours == null ? 1e9 : Number(b.reach_hours);
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
      tdN.textContent = b.name;
      const tdNext = document.createElement("td");
      tdNext.className = "tabular";
      tdNext.textContent = `L${b.current_level} → L${b.next_level}`;
      const tdTime = document.createElement("td");
      tdTime.className = "tabular";
      tdTime.textContent = b.build_time_text;
      const tdReach = document.createElement("td");
      tdReach.className = "col-right tabular";
      const pill = document.createElement("span");
      pill.className = `eta-pill${b.reach_eta === "—" ? " is-blocked" : b.wait_hours > 0 ? " is-wait" : ""}`;
      pill.textContent = b.reach_eta;
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
  if (val === null || val === undefined || val === "" || typeof val === "boolean") {
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
  return Number.isInteger(n) ? fmtInt(n) : n.toFixed(1);
}

function fmtImprovementPct(rawPct, baseFit, bestFit) {
  let pct = finiteNum(rawPct, null);
  if (pct === null && baseFit !== null && bestFit !== null) {
    if (Math.abs(baseFit) > 1e-6) {
      pct = ((bestFit - baseFit) / Math.abs(baseFit)) * 100;
    } else if (Math.abs(bestFit) <= 1e-12) {
      pct = 0;
    } else {
      pct = bestFit > 0 ? 999.9 : -999.9;
    }
  }
  if (pct === null) return "0.0%";
  if (pct > 999.9) return ">+999.9%";
  if (pct < -999.9) return "<-999.9%";
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function geneShortTag(kind) {
  const k = String(kind || "").trim().toUpperCase();
  if (!k) return "";
  if (k === "BUILD") return "BLD";
  if (k === "WAIT_RESOURCES") return "WAIT";
  if (k === "RESEARCH") return "RES";
  if (k === "BUILD_SHIP") return "SHP";
  if (k === "ATTACK") return "ATK";
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
  const metaLine = el("gann-meta-line");
  if (metaLine) {
    metaLine.textContent =
      elapsedMs !== null
        ? `POP ${popSize} · ${nnBackend} · ${Math.round(elapsedMs)}ms`
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
  const loss = finiteNum(rawSum.nn_final_loss, null);
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
  const parts = [
    `Prerequisites: ${unmet.length === 0 ? "Unlocked" : unmet.join(", ")}`,
    `Next L${selected.next_level} Cost: ${formatResourcesInline(selected.cost)}`,
    `Resources Ready: ${selected.wait_eta}${selected.bottleneck ? ` (${bottleneckTag(selected.bottleneck)} bottleneck)` : ""}`,
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
  const bfUsed = Number(pEcon.blast_furnace_pig_used_per_hr || 0);
  const bfPct = Number(pEcon.blast_furnace_pct ?? 100);
  const missPig = Number(selected.missing?.pig_iron || 0);
  if (selected.bottleneck === "pig_iron" && bfPct > 0 && bfUsed > 0 && missPig > 0) {
    const newRate = Number(pEcon.pig_iron_per_hr || 0) + bfUsed;
    const oldWait = selected.wait_eta;
    const altWait = newRate > 0 ? `${(missPig / newRate).toFixed(1)}h` : "Blocked";
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
}

function renderBuildingsView(dash) {
  const etaData = currentBuildingsEta(dash);
  const tbody = el("eta-table-tbody");
  if (!tbody || !etaData) return;

  const p = etaData.planet || {};
  const econ = etaData.planet_economy || {};
  el("bld-planet-meta").textContent =
    `[${p.coords || "--"}] · Dev L${p.dev_center_level ?? 0} · Lab L${p.research_lab_level ?? 0} · Micro L${p.microsystem_level ?? 0} · Div x${p.building_time_divisor ?? 1}`;

  const bfPct = Number(econ.blast_furnace_pct ?? getSelectedBlastFurnacePct(dash));
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
        title.textContent = row.name;
        const sub = document.createElement("span");
        sub.className = "action-sub tabular";
        sub.textContent = ` (${row.key})`;
        title.appendChild(sub);
        if (row.queued_count > 0 || row.active) {
          const qBadge = document.createElement("span");
          qBadge.className = "rate-chip";
          qBadge.textContent = row.active ? "ACTIVE" : `Q x${row.queued_count}`;
          title.appendChild(qBadge);
        }
        if (row.economy_rate_pct != null && row.current_level > 0) {
          const rChip = document.createElement("span");
          rChip.className = `rate-chip tabular${row.economy_rate_pct < 100 ? " is-paused" : ""}`;
          rChip.textContent = `${row.economy_rate_pct}%`;
          title.appendChild(rChip);
        }
        tdUnit.appendChild(title);

        const tdLvl = document.createElement("td");
        tdLvl.className = "col-right tabular";
        tdLvl.textContent = String(row.current_level);

        const tdNext = document.createElement("td");
        tdNext.className = "col-right tabular";
        tdNext.textContent = `L${row.next_level}`;

        const tdMiss = document.createElement("td");
        tdMiss.className = "tabular";
        if (row.missing_total === 0) {
          const ok = document.createElement("span");
          ok.className = "status-word";
          ok.textContent = "Affordable";
          tdMiss.appendChild(ok);
        } else {
          tdMiss.textContent = formatResourcesInline(row.missing);
        }

        const tdBuildT = document.createElement("td");
        tdBuildT.className = "col-right tabular";
        tdBuildT.textContent = row.build_time_text;

        const tdReady = document.createElement("td");
        tdReady.className = "col-right tabular";
        tdReady.textContent = row.bottleneck ? `${row.wait_eta} (${bottleneckTag(row.bottleneck)})` : row.wait_eta;

        const tdReach = document.createElement("td");
        tdReach.className = "col-right tabular";
        const rSpan = document.createElement("span");
        rSpan.className = `eta-pill${row.reach_eta === "—" ? " is-blocked" : row.wait_hours > 0 ? " is-wait" : ""}`;
        rSpan.textContent = row.reach_eta;
        tdReach.appendChild(rSpan);

        const tdCtl = document.createElement("td");
        tdCtl.className = "col-right";
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
          tdCtl.appendChild(bBtn);
        } else if (row.unmet_prerequisites && row.unmet_prerequisites.length > 0) {
          const lock = document.createElement("span");
          lock.className = "action-sub";
          lock.textContent = "Locked";
          tdCtl.appendChild(lock);
        } else {
          const insp = document.createElement("span");
          insp.className = "action-sub";
          insp.textContent = row.offer_visible ? "Wait" : "Forecast";
          tdCtl.appendChild(insp);
        }

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
  el("cmb-power-val").textContent = `${fmtInt(Math.round(cmb.stationed_fleet_power || 0))} / ${fmtInt(Math.round(cmb.stationed_defense_power || 0))}`;
  el("cmb-cargo-val").textContent = `Cargo ${fmtInt(cmb.total_cargo_capacity || 0)} · Weapons Fac L${cmb.weapons_factory_level ?? 0}`;

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
      if (offer && offer.can_build) {
        const wrap = document.createElement("div");
        wrap.className = "row-actions";
        const inp = document.createElement("input");
        inp.type = "number";
        inp.min = "1";
        inp.value = "1";
        inp.className = "qty-inline";
        inp.setAttribute("aria-label", `Order quantity for ${s.name}`);

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-primary btn-sm";
        btn.textContent = "Build";
        btn.addEventListener("click", async () => {
          const qty = Math.max(1, Number(inp.value || 1));
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
        tdOrd.appendChild(wrap);
      } else {
        const st = document.createElement("span");
        st.className = "action-sub";
        st.textContent = offer ? offer.status_text || "Locked" : "Sync Shipyard";
        tdOrd.appendChild(st);
      }

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
        Number(resMap.pig_iron || 0) +
        Number(resMap.metal || 0) +
        Number(resMap.kryptonite || 0) +
        Number(resMap.spice || 0);
      const tdLoot = document.createElement("td");
      tdLoot.className = "col-right tabular";
      tdLoot.textContent = totalRes > 0 ? fmtInt(totalRes) : "Unspied";

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
    const val = Math.max(0, Number(el(`sim-atk-${key}`)?.value || 0));
    if (val > 0) atk[key] = val;
  }
  const defShips = {};
  const s4 = Math.max(0, Number(el("sim-def-s4")?.value || 0));
  if (s4 > 0) defShips.s4 = s4;

  const defDefs = {};
  for (const dk of ["d1", "d2", "d4"]) {
    const val = Math.max(0, Number(el(`sim-def-${dk}`)?.value || 0));
    if (val > 0) defDefs[dk] = val;
  }
  const totalLoot = Math.max(0, Number(el("sim-loot-total")?.value || 0));
  const split = Math.floor(totalLoot / 4);
  const margin = Math.max(1.0, Number(el("sim-margin")?.value || 1.4));

  try {
    const res = await apiCommand({
      command: "simulate_combat",
      attacker_ships: atk,
      defender_ships: defShips,
      defender_defense: defDefs,
      target_resources: { pig_iron: split, metal: split, kryptonite: split, spice: totalLoot - split * 3 },
      safety_margin: margin,
    });
    const sim = res.simulation;
    const badge = el("sim-verdict-badge");
    badge.textContent = sim.safe_to_attack ? "SAFE TO RAID" : "UNSAFE / HOLD";
    badge.className = `run-badge tabular${sim.safe_to_attack ? "" : " is-paused"}`;
    const ratioStr = sim.win_ratio === Infinity ? "INF" : Number(sim.win_ratio || 0).toFixed(2);
    el("sim-ratio-text").textContent = `Power Ratio: ${ratioStr}x (Threshold ${sim.safety_margin}x)`;
    el("sim-atk-pwr").textContent = fmtInt(Math.round(sim.attacker_power || 0));
    el("sim-def-pwr").textContent = fmtInt(Math.round(sim.defender_power || 0));
    el("sim-cargo").textContent = fmtInt(sim.cargo_capacity || 0);
    el("sim-plunder").textContent = `${fmtInt(sim.total_plunder || 0)} (${fmtInt(Math.round(sim.loot_spice_equivalent || 0))} SpEq)`;
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
  ]) {
    const box = el(`cfg-${field}`);
    if (box) box.checked = Boolean(cfg[field]);
  }
  el("cfg-storage_warn_ratio").value = cfg.storage_warn_ratio ?? 0.85;
  el("cfg-combat_safety_margin").value = cfg.combat_safety_margin ?? 1.4;
  el("cfg-fleet_save_fuel_reserve_spice").value = cfg.fleet_save_fuel_reserve_spice ?? 500;
  el("cfg-max_actions_per_hour").value = cfg.max_actions_per_hour ?? 30;
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

  if (!samples || samples.length === 0) {
    const txt = document.createElementNS(ns, "text");
    txt.setAttribute("x", String(w / 2));
    txt.setAttribute("y", String(h / 2));
    txt.setAttribute("text-anchor", "middle");
    txt.setAttribute("fill", "#9eb3b8");
    txt.setAttribute("font-size", "11");
    txt.textContent = "Collecting samples into state/rank_history.json...";
    svg.appendChild(txt);
    return;
  }

  const vals = samples.map((s) => Number(s.estimated_points || 0));
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const span = Math.max(0.5, maxV - minV);

  const toXY = (idx, val) => {
    const x = samples.length === 1 ? w / 2 : padX + (idx / (samples.length - 1)) * (w - padX * 2);
    const y = h - padY - ((val - minV) / span) * (h - padY * 2);
    return [x.toFixed(1), y.toFixed(1)];
  };

  if (samples.length >= 2) {
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

  if (badge) {
    badge.textContent = cur.server_rank != null ? `#${cur.server_rank}` : az.label || "—";
  }

  const srvRankTxt = cur.server_rank != null ? `#${fmtInt(cur.server_rank)}` : "Rank pending";
  const srvPtsTxt = cur.server_points != null ? `${fmtInt(cur.server_points)} pts` : "— pts";
  el("rnk-server-pos").textContent = `${srvRankTxt} · ${srvPtsTxt}`;
  el("rnk-est-pts").textContent = `~${Number(cur.estimated_points || 0).toFixed(1)} est. score (bld/res/flt)`;

  el("rnk-az-label").textContent = az.label || "—";
  const shield = cur.newbie_protected ? `Shielded (<10k · ${cur.newbie_eta_compound || "—"})` : "Open AZ";
  el("rnk-az-range").textContent = `${az.range_text || ""} · ${shield}`;

  el("rnk-rate-hr").textContent = `+${Number(rates.pts_per_hr_steady || 0).toFixed(2)} pts/hr`;
  el("rnk-res-hr").textContent = `${fmtInt(Math.round(rates.net_resources_per_hr || 0))} res/hr · x${rates.speedup_factor || 1}`;

  const totalEst = Math.max(0.01, Number(cur.estimated_points || 0));
  const bPct = Math.round((Number(cur.buildings_points || 0) / totalEst) * 100);
  const rPct = Math.round((Number(cur.research_points || 0) / totalEst) * 100);
  const fPct = Math.max(0, 100 - bPct - rPct);
  el("rnk-breakdown").textContent = `Bld ${bPct}% · Res ${rPct}% · Flt ${fPct}%`;
  el("rnk-spice-val").textContent = `${fmtInt(Math.round(cur.spice_value || 0))} Spice Eq`;

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
          tdS.textContent = `${fmtInt(Math.round(row.steady_points))} (+${fmtInt(Math.round(row.steady_delta))})`;
          const tdSAz = document.createElement("td");
          tdSAz.className = "col-right tabular";
          tdSAz.textContent = row.steady_az;
          const tdC = document.createElement("td");
          tdC.className = "col-right tabular";
          tdC.textContent = `${fmtInt(Math.round(row.compound_points))} (+${fmtInt(Math.round(row.compound_delta))})`;
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
          tdN.textContent = `${m.label} (${fmtInt(m.target_points)})`;
          const tdM = document.createElement("td");
          tdM.className = "col-right tabular";
          tdM.textContent = `+${fmtInt(Math.round(m.missing_points))}`;
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

  const count = Number(trend.sample_count || 0);
  el("rnk-trend-meta").textContent = `${count} sample${count === 1 ? "" : "s"}`;
  if (count >= 2 && trend.delta_points != null) {
    const sign = trend.delta_points >= 0 ? "+" : "";
    const rankStr =
      trend.rank_climbed != null
        ? ` · Rank ${trend.rank_climbed >= 0 ? `climbed +${trend.rank_climbed}` : trend.rank_climbed}`
        : "";
    el("rnk-observed-rate").textContent =
      trend.observed_pts_per_hr != null ? `${sign}${trend.observed_pts_per_hr} pts/hr observed` : `${sign}${trend.delta_points} pts`;
    el("rnk-observed-summary").textContent =
      `Window ${trend.elapsed_hours}h: ${sign}${trend.delta_points} pts${rankStr}.`;
  } else {
    el("rnk-observed-rate").textContent = "Collecting samples...";
    el("rnk-observed-summary").textContent =
      "Rate delta activates automatically after 2 distinct observations.";
  }

  renderRankingChart(trend.samples || []);
}

function formatDbSummary(db) {
  if (!db) return "SQLite database disabled (no persistent state directory configured).";
  const lines = [
    `DB Path: ${db.db_path} (${fmtInt(db.size_bytes)} bytes)`,
    `Row Counts: ${Object.entries(db.counts || {}).map(([k, v]) => `${k}=${v}`).join(" · ")}`,
    "",
    "== Tutorial Progress ==",
  ];
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

function onPlanetChanged() {
  if (!state.dashboard) return;
  renderColonyMatrix(state.dashboard);
  renderTelemetry(state.dashboard);
  renderDashNearestEtas(state.dashboard);
  refreshBuildingsEtaForSelectedPlanet();
  if (state.activePage === "combat") {
    refreshCombatForSelectedPlanet({ refresh: false });
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
    if (!state.configDirty) {
      renderConfigForm(dash.config);
      state.configDirty = false;
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
    state.gaCalcRunning = true;
    renderGaNnHud(state.dashboard);
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
      state.gaCalcRunning = false;
      renderGaNnHud(state.dashboard);
    }
  });

  el("btn-sync").addEventListener("click", async () => {
    const isGa = String(state.dashboard?.config?.planner_mode || "").toLowerCase() === "ga_nn";
    if (isGa) {
      state.gaCalcRunning = true;
      renderGaNnHud(state.dashboard);
    }
    try {
      showFeedback("Syncing live snapshot from Chrome...", "ok");
      const ok = await loadDashboard({ refresh: true, preserveConfig: true, force: true });
      if (ok) {
        if (state.dashboard?.health?.rate_limited) {
          const rem = Math.ceil(Number(state.dashboard.health.retry_after || 0));
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
        state.gaCalcRunning = false;
        renderGaNnHud(state.dashboard);
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
      state.gaCalcRunning = true;
      renderGaNnHud(state.dashboard);
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
        state.gaCalcRunning = false;
        renderGaNnHud(state.dashboard);
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
    };
    for (const numField of [
      "storage_warn_ratio",
      "combat_safety_margin",
      "fleet_save_fuel_reserve_spice",
      "max_actions_per_hour",
      "ga_population_size",
      "ga_generations",
      "ga_horizon_steps",
    ]) {
      const inp = el(`cfg-${numField}`);
      const raw = inp ? String(inp.value ?? "").trim() : "";
      if (raw !== "") {
        payload[numField] = Number(raw);
      }
    }
    const seedInp = el("cfg-ga_seed");
    if (seedInp) {
      const rawSeed = String(seedInp.value ?? "").trim();
      payload.ga_seed = rawSeed === "" ? null : Number(rawSeed);
    }

    const isGa = String(plannerModeVal).toLowerCase() === "ga_nn";
    if (isGa) {
      state.gaCalcRunning = true;
      renderGaNnHud(state.dashboard);
    }
    try {
      const res = await apiCommand({ command: "update_config", config: payload });
      state.dashboardSeq += 1;
      state.configDirty = false;
      renderConfigForm(res.config);
      showFeedback("Saved config.yaml — Commander & API updated.", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false, force: true });
    } catch (err) {
      showFeedback(`Failed saving config: ${err.message}`, feedbackKindForError(err));
    } finally {
      if (isGa) {
        state.gaCalcRunning = false;
        renderGaNnHud(state.dashboard);
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
}

function applySavedTabButtons() {
  document.querySelectorAll("[data-cmb-tab]").forEach((b) => b.classList.toggle("active", b.dataset.cmbTab === state.combatSubTab));
  el("cmb-pane-ships").hidden = state.combatSubTab !== "ships";
  el("cmb-pane-defense").hidden = state.combatSubTab !== "defense";
  el("cmb-pane-targets").hidden = state.combatSubTab !== "targets";
  el("cmb-pane-escape").hidden = state.combatSubTab !== "escape";

  document.querySelectorAll("[data-filter]").forEach((c) => c.classList.toggle("active", c.dataset.filter === state.queueFilter));
  document.querySelectorAll("[data-log]").forEach((b) => b.classList.toggle("active", b.dataset.log === state.activeLogSource));
}

restorePersistedUiState();
wireEvents();
applySavedTabButtons();
const initialHash = window.location.hash.replace(/^#/, "");
setActivePage(PAGE_TITLES[initialHash] ? initialHash : state.activePage);
checkApiHealthAndHotReload();
loadDashboard({ refresh: false, preserveConfig: false });

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
