const state = {
  dashboard: null,
  selectedPlanetId: null,
  activeTab: "queue",
  queueFilter: "all",
  activeLogSource: "commander",
  busy: false,
};

const el = (id) => document.getElementById(id);

function fmtInt(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function showFeedback(message, kind = "ok") {
  const banner = el("feedback-banner");
  banner.textContent = message;
  banner.className = `feedback-banner ${kind === "error" ? "is-error" : "is-ok"}`;
  banner.hidden = false;
}

async function apiGet(url) {
  const res = await fetch(url, { cache: "no-store" });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

async function apiCommand(payload) {
  const res = await fetch("/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function activePlanetFrom(dash) {
  const gs = dash?.state;
  if (!gs || !Array.isArray(gs.planets) || gs.planets.length === 0) return null;
  const targetId = state.selectedPlanetId || gs.active_planet_id;
  return gs.planets.find((p) => p.planet_id === targetId) || gs.planets[0];
}

function renderTopbar(dash) {
  const health = dash.health || {};
  const pip = el("chrome-pip");
  const statusText = el("chrome-status-text");
  if (health.in_game) {
    pip.className = "status-pip online";
    statusText.textContent = `Chrome :${health.debug_port || 9222} connected · In Game (${dash.state?.nickname || "RETRO"})`;
  } else if (health.debug_port_open) {
    pip.className = "status-pip";
    statusText.textContent = `Chrome :${health.debug_port || 9222} open · Outside game shell`;
  } else {
    pip.className = "status-pip offline";
    statusText.textContent = `Chrome :${health.debug_port || 9222} offline`;
  }

  const botCtrl = dash.bot_control || {};
  const paused = Boolean(botCtrl.paused);
  const pauseBtn = el("btn-pause-toggle");
  pauseBtn.setAttribute("aria-pressed", paused ? "true" : "false");
  pauseBtn.classList.toggle("btn-warning", paused);
  el("pause-label").textContent = paused ? "Resume Auto" : "Pause Auto";

  el("mode-select").value = botCtrl.mode_override || "auto";

  const planetSel = el("planet-select");
  const planets = dash.state?.planets || [];
  if (planets.length > 0) {
    const chosen = activePlanetFrom(dash)?.planet_id;
    planetSel.innerHTML = "";
    for (const p of planets) {
      const opt = document.createElement("option");
      opt.value = String(p.planet_id);
      opt.textContent = `${p.coords} ${p.name}`;
      if (p.planet_id === chosen) opt.selected = true;
      planetSel.appendChild(opt);
    }
  }
}

function renderResourceBar(key, amount, storage) {
  const cap = Number(storage || 0);
  const val = Number(amount || 0);
  const pct = cap > 0 ? Math.min(100, Math.round((val / cap) * 100)) : 0;
  el(`val-${key}`).textContent = fmtInt(val);
  el(`cap-${key}`).textContent = cap > 0 ? `/ ${fmtInt(cap)} (${pct}%)` : "—";
  const bar = el(`bar-${key}`);
  bar.style.transform = `scaleX(${pct / 100})`;
  bar.classList.toggle("high", pct >= 85);
}

function renderTelemetry(dash) {
  const planet = activePlanetFrom(dash);
  if (planet) {
    renderResourceBar("pig", planet.resources.pig_iron, planet.storage.pig_iron);
    renderResourceBar("metal", planet.resources.metal, planet.storage.metal);
    renderResourceBar("kryp", planet.resources.kryptonite, planet.storage.kryptonite);
    renderResourceBar("spice", planet.resources.spice, planet.storage.spice);

    const produced = Number(planet.energy?.produced || 0);
    const used = Number(planet.energy?.used || 0);
    const net = produced - used;
    el("val-energy").textContent = `${net >= 0 ? "+" : ""}${fmtInt(net)}`;
    el("energy-ratio").textContent = `${fmtInt(produced)} / ${fmtInt(used)}`;
    el("planet-specs").textContent = `Temp ${planet.temperature}°C · Fields ${planet.fields_used}/${planet.fields_max}`;
  }

  const health = dash.health || {};
  el("val-budget").textContent = `${health.remaining_hourly_actions ?? "—"} clicks/hr`;
  el("val-speedup").textContent = dash.state?.speedup_factor ? `Speed x${dash.state.speedup_factor}` : "Standard";
  const hostileCount = (dash.state?.fleet_alerts || []).filter((a) => a.hostile).length;
  el("val-alerts").textContent = hostileCount > 0 ? `${hostileCount} hostile incoming!` : "No hostile alerts";

  const tut = dash.state?.tutorial;
  const tutBar = el("tutorial-strip");
  if (tut) {
    tutBar.hidden = false;
    el("tutorial-title").textContent = `Tutorial ${tut.step} / ${tut.total_steps}`;
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
  if (action.kind === "ESCAPE_FLIGHT") {
    return { command: "fleetsave_plan", planet_id: action.planet_id };
  }
  return null;
}

function renderPlanTable(dash) {
  const tbody = el("plan-tbody");
  let actions = dash.plan || [];
  if (state.queueFilter === "ready") {
    actions = actions.filter((a) => a.executable_now);
  }
  if (actions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">No matching actions in queue.</td></tr>';
    return;
  }

  tbody.innerHTML = "";
  for (const act of actions) {
    const tr = document.createElement("tr");

    const tdPrio = document.createElement("td");
    const prioTag = document.createElement("span");
    prioTag.className = `prio-tag prio-${act.priority}`;
    const prioDot = document.createElement("span");
    prioDot.className = "prio-dot";
    prioTag.append(prioDot, document.createTextNode(String(act.priority || "?")));
    tdPrio.appendChild(prioTag);
    tr.appendChild(tdPrio);

    const tdTarget = document.createElement("td");
    const lvlStr = act.target_level ? ` → L${act.target_level}` : "";
    const titleDiv = document.createElement("div");
    titleDiv.className = "action-title";
    titleDiv.textContent = `${act.target_name}${lvlStr}`;
    const subDiv = document.createElement("div");
    subDiv.className = "action-sub";
    subDiv.textContent = `${act.kind} · [${act.planet_coords}]`;
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

    const tdReason = document.createElement("td");
    tdReason.textContent = act.reason;
    tr.appendChild(tdReason);

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
      waitTag.textContent = act.kind === "RESEARCH" || act.kind === "SHIP" ? "Advisory / Wait" : "Waiting";
      tdOps.appendChild(waitTag);
    }
    tr.appendChild(tdOps);
    tbody.appendChild(tr);
  }
}

function renderLevels(dash) {
  const planet = activePlanetFrom(dash);
  el("levels-planet-label").textContent = planet ? `${planet.coords} ${planet.name}` : "—";

  const bBody = el("buildings-tbody");
  bBody.innerHTML = "";
  if (planet) {
    const entries = Object.entries(planet.buildings || {}).sort(
      (a, b) => Number(a[0].slice(1)) - Number(b[0].slice(1))
    );
    for (const [bkey, lvl] of entries) {
      const tr = document.createElement("tr");
      const name = planet.building_names?.[bkey] || bkey;
      const tdName = document.createElement("td");
      tdName.textContent = `${name} `;
      const codeSpan = document.createElement("span");
      codeSpan.className = "action-sub";
      codeSpan.textContent = `(${bkey})`;
      tdName.appendChild(codeSpan);
      const tdLvl = document.createElement("td");
      tdLvl.className = "col-right tabular";
      tdLvl.textContent = String(lvl);
      tr.append(tdName, tdLvl);
      bBody.appendChild(tr);
    }
  }

  const rBody = el("research-tbody");
  rBody.innerHTML = "";
  const researches = dash.state?.researches || {};
  const rNames = dash.state?.research_names || {};
  for (const [rkey, lvl] of Object.entries(researches).sort((a, b) => Number(a[0].slice(1)) - Number(b[0].slice(1)))) {
    const tr = document.createElement("tr");
    const tdName = document.createElement("td");
    tdName.textContent = `${rNames[rkey] || rkey} `;
    const codeSpan = document.createElement("span");
    codeSpan.className = "action-sub";
    codeSpan.textContent = `(${rkey})`;
    tdName.appendChild(codeSpan);
    const tdLvl = document.createElement("td");
    tdLvl.className = "col-right tabular";
    tdLvl.textContent = String(lvl);
    tr.append(tdName, tdLvl);
    rBody.appendChild(tr);
  }
}

function renderConfigForm(cfg) {
  if (!cfg) return;
  el("cfg-playstyle").value = cfg.playstyle || "fleeter";
  for (const field of [
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
    const node = el(`cfg-${field}`);
    if (node) node.checked = Boolean(cfg[field]);
  }
  el("cfg-storage_warn_ratio").value = cfg.storage_warn_ratio ?? 0.85;
  el("cfg-combat_safety_margin").value = cfg.combat_safety_margin ?? 1.4;
  el("cfg-fleet_save_fuel_reserve_spice").value = cfg.fleet_save_fuel_reserve_spice ?? 500;
  el("cfg-max_actions_per_hour").value = cfg.max_actions_per_hour ?? 60;
}

function renderLogs(logs) {
  if (!logs) return;
  const text = state.activeLogSource === "api" ? logs.game_api : logs.commander;
  const node = el("log-output");
  node.textContent = text || "(no log lines yet)";
  node.scrollTop = node.scrollHeight;
}

async function loadDashboard({ refresh = false, preserveConfig = true } = {}) {
  try {
    const dash = await apiGet(`/dashboard?refresh=${refresh ? 1 : 0}`);
    const firstLoad = state.dashboard === null;
    state.dashboard = dash;
    renderTopbar(dash);
    renderTelemetry(dash);
    renderPlanTable(dash);
    renderLevels(dash);
    if (firstLoad || !preserveConfig) {
      renderConfigForm(dash.config);
    }
    renderLogs(dash.logs);
  } catch (err) {
    showFeedback(`Dashboard refresh error: ${err.message}`, "error");
  }
}

async function dispatchAction(action, dryRun) {
  const payload = actionPayloadFor(action, dryRun);
  if (!payload) return;
  try {
    showFeedback(`${dryRun ? "Simulating" : "Executing"} ${action.target_name}…`, "ok");
    const res = await apiCommand(payload);
    showFeedback(res.message || "Command completed.", "ok");
    await loadDashboard({ refresh: false });
  } catch (err) {
    showFeedback(`Dispatch error: ${err.message}`, "error");
  }
}

function wireEvents() {
  el("btn-sync").addEventListener("click", async () => {
    showFeedback("Syncing live snapshot from Chrome…", "ok");
    await loadDashboard({ refresh: true, preserveConfig: true });
    showFeedback("Synced with live Chrome session.", "ok");
  });

  el("btn-tick-now").addEventListener("click", async () => {
    try {
      const mode = el("mode-select").value;
      const dryRun = mode === "dry-run";
      showFeedback(`Running 1 ${dryRun ? "dry-run" : "live"} tick…`, "ok");
      const res = await apiCommand({ command: "tick_once", dry_run: dryRun });
      showFeedback(res.message || "Tick finished.", "ok");
      await loadDashboard({ refresh: false });
    } catch (err) {
      showFeedback(`Tick failed: ${err.message}`, "error");
    }
  });

  el("btn-pause-toggle").addEventListener("click", async () => {
    const currentPaused = Boolean(state.dashboard?.bot_control?.paused);
    try {
      await apiCommand({ command: "bot_control", paused: !currentPaused });
      await loadDashboard({ refresh: false });
      showFeedback(!currentPaused ? "Commander auto-loop paused." : "Commander auto-loop resumed.", "ok");
    } catch (err) {
      showFeedback(`Pause update failed: ${err.message}`, "error");
    }
  });

  el("mode-select").addEventListener("change", async (e) => {
    const val = e.target.value;
    try {
      await apiCommand({
        command: "bot_control",
        mode_override: val === "auto" ? null : val,
      });
      await loadDashboard({ refresh: false });
      showFeedback(`Loop mode set to ${val}.`, "ok");
    } catch (err) {
      showFeedback(`Mode update failed: ${err.message}`, "error");
    }
  });

  el("planet-select").addEventListener("change", (e) => {
    state.selectedPlanetId = Number(e.target.value);
    if (state.dashboard) {
      renderTelemetry(state.dashboard);
      renderLevels(state.dashboard);
    }
  });

  el("btn-claim-tutorial").addEventListener("click", async () => {
    try {
      const res = await apiCommand({ command: "claim_tutorial", dry_run: false });
      showFeedback(res.message || "Tutorial claim submitted.", "ok");
      await loadDashboard({ refresh: true });
    } catch (err) {
      showFeedback(`Tutorial claim failed: ${err.message}`, "error");
    }
  });

  el("btn-save-config").addEventListener("click", async () => {
    const payload = {
      playstyle: el("cfg-playstyle").value,
      allow_research: el("cfg-allow_research").checked,
      allow_shipyard: el("cfg-allow_shipyard").checked,
      allow_multi_planet: el("cfg-allow_multi_planet").checked,
      merchant_mode: el("cfg-merchant_mode").checked,
      allow_fleetsave: el("cfg-allow_fleetsave").checked,
      allow_galaxy_scan: el("cfg-allow_galaxy_scan").checked,
      allow_raids: el("cfg-allow_raids").checked,
      allow_pvp_in_az: el("cfg-allow_pvp_in_az").checked,
      allow_colonize: el("cfg-allow_colonize").checked,
      storage_warn_ratio: Number(el("cfg-storage_warn_ratio").value),
      combat_safety_margin: Number(el("cfg-combat_safety_margin").value),
      fleet_save_fuel_reserve_spice: Number(el("cfg-fleet_save_fuel_reserve_spice").value),
      max_actions_per_hour: Number(el("cfg-max_actions_per_hour").value),
    };
    try {
      const res = await apiCommand({ command: "update_config", config: payload });
      renderConfigForm(res.config);
      showFeedback("Saved config.yaml — Commander & API updated.", "ok");
      await loadDashboard({ refresh: false, preserveConfig: false });
    } catch (err) {
      showFeedback(`Failed saving config: ${err.message}`, "error");
    }
  });

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
        showFeedback(`Navigation failed: ${err.message}`, "error");
      }
    });
  });

  document.querySelectorAll("[data-tab]").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      state.activeTab = tabBtn.dataset.tab;
      document.querySelectorAll("[data-tab]").forEach((b) => {
        const active = b.dataset.tab === state.activeTab;
        b.classList.toggle("active", active);
        b.setAttribute("aria-selected", active ? "true" : "false");
      });
      el("panel-queue").hidden = state.activeTab !== "queue";
      el("panel-fleet").hidden = state.activeTab !== "fleet";
      el("panel-levels").hidden = state.activeTab !== "levels";
      el("queue-filters").hidden = state.activeTab !== "queue";
    });
  });

  document.querySelectorAll("[data-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.queueFilter = chip.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((c) => c.classList.toggle("active", c === chip));
      if (state.dashboard) renderPlanTable(state.dashboard);
    });
  });

  document.querySelectorAll("[data-log]").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      state.activeLogSource = tabBtn.dataset.log;
      document.querySelectorAll("[data-log]").forEach((b) => b.classList.toggle("active", b === tabBtn));
      if (state.dashboard) renderLogs(state.dashboard.logs);
    });
  });

  el("btn-refresh-logs").addEventListener("click", () => loadDashboard({ refresh: false }));
}

wireEvents();
loadDashboard({ refresh: false, preserveConfig: false });
setInterval(() => {
  if (document.visibilityState === "visible") {
    loadDashboard({ refresh: false, preserveConfig: true });
  }
}, 5000);
