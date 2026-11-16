import React, { useMemo, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import {
  useActivePage,
  useActivePlanet,
  useCombatData,
  useConsoleActions,
  useDashboard,
  useUiPrefs,
} from "../../store";
import { finiteNum, fmtInt } from "../../utils/formatters";
import { CombatShipyardPane } from "./CombatShipyardPane";
import { CombatSimulator } from "./CombatSimulator";
import { CombatTargetsPane } from "./CombatTargetsPane";
import { EscapeFlightPane } from "./EscapeFlightPane";

export interface CombatViewProps {
  hidden?: boolean;
}

export const CombatView: React.FC<CombatViewProps> = ({ hidden }) => {
  const dashboard = useDashboard();
  const activePage = useActivePage();
  const activePlanet = useActivePlanet();
  const combatData = useCombatData();
  const uiPrefs = useUiPrefs();
  const {
    refreshCombatForSelectedPlanet,
    enqueueManualCommand,
    showFeedback,
    apiCommand,
    loadDashboard,
    setUiPref,
  } = useConsoleActions();

  const [shipQtys, setShipQtys] = useState<Record<number, string>>({});

  const isHidden = hidden ?? activePage !== "combat";
  const subTab = uiPrefs.combatSubTab || "ships";

  const cmb = useMemo(() => {
    if (combatData && activePlanet && combatData.planet_id === activePlanet.planet_id) {
      return combatData;
    }
    return dashboard?.combat || combatData || null;
  }, [combatData, activePlanet, dashboard?.combat]);

  const az = cmb?.attack_zone || {};
  const techs = cmb?.combat_techs || {};
  const fltPow = finiteNum(cmb?.stationed_fleet_power, 0);
  const defPow = finiteNum(cmb?.stationed_defense_power, 0);
  const cargoCap = finiteNum(cmb?.total_cargo_capacity, 0);

  const ships: Array<Record<string, any>> = Array.isArray(cmb?.ships) ? cmb!.ships : [];
  const defenses: Array<Record<string, any>> = Array.isArray(cmb?.defenses) ? cmb!.defenses : [];
  const offersById = useMemo(
    () =>
      new Map<number, Record<string, any>>(
        Array.isArray(cmb?.ship_offers)
          ? cmb!.ship_offers.map((o: any) => [Number(o.unit_id), o])
          : []
      ),
    [cmb?.ship_offers]
  );

  const raidTargets = useMemo(() => {
    const ownCoords = new Set(
      (dashboard?.state?.planets || []).map((p) => String(p.coords || "")).filter(Boolean)
    );
    const rawTargets: Array<Record<string, any>> =
      dashboard?.db_summary?.spy_targets || dashboard?.state?.raid_targets || [];
    return rawTargets.filter((t) => {
      const coords = String(t?.coords || t?.target_coords || "");
      return Boolean(coords) && !ownCoords.has(coords) && t?.status_color !== "free";
    });
  }, [
    dashboard?.db_summary?.spy_targets,
    dashboard?.state?.planets,
    dashboard?.state?.raid_targets,
  ]);

  const handleNavJump = async (action: string) => {
    try {
      await apiCommand({ command: "navigate", action });
      showFeedback(`Chrome switched to ${action}.`, "ok");
    } catch (err: any) {
      showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleScanGalaxy = async () => {
    try {
      showFeedback("Scanning home Galaxy system in Chrome...", "ok");
      const res = await apiCommand({
        command: "scan_galaxy",
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      setUiPref("combatSubTab", "targets");
      showFeedback(res.message || "Galaxy scan completed.", "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Galaxy scan failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleRefreshCombat = async () => {
    showFeedback("Refreshing live Shipyard offers from Chrome...", "ok");
    const ok = await refreshCombatForSelectedPlanet({ refresh: true });
    if (ok) {
      showFeedback("Fleet Build & Shipyard state refreshed.", "ok");
    }
  };

  const handleBuildShip = async (ship: Record<string, any>) => {
    const unitId = Number(ship.unit_id);
    const qty = Math.max(1, Math.round(finiteNum(shipQtys[unitId], 1)));
    try {
      const res = await apiCommand({
        command: "build_ship",
        ship_id: unitId,
        quantity: qty,
        planet_id: cmb?.planet_id ?? activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(res.message || `Ordered ${qty}x ${ship.name}.`, "ok");
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Ship order failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  const handleQueueShip = (ship: Record<string, any>) => {
    const unitId = Number(ship.unit_id);
    const qty = Math.max(1, Math.round(finiteNum(shipQtys[unitId], 1)));
    enqueueManualCommand({
      kind: "BUILD_SHIP",
      priority: "P1",
      position: "top",
      planet_id: cmb?.planet_id || activePlanet?.planet_id || 0,
      target_id: unitId,
      target_name: String(ship.name || ""),
      target_level: qty,
    });
  };

  const handleAttackTarget = async (coords: string) => {
    try {
      showFeedback(`Sending attack to ${coords}...`, "ok");
      const res = await apiCommand({
        command: "attack",
        target_coords: coords,
        planet_id: cmb?.planet_id ?? activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Attack processed for ${coords}.`,
        res.executed ? "ok" : "warn"
      );
      await loadDashboard({ refresh: false, force: true });
    } catch (err: any) {
      showFeedback(`Attack failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-combat"
      className="page-view"
      aria-label="Fleet Build — shipyard, defense construction, escape flight, and 1:1 combat simulator"
      hidden={isHidden}
    >
      <div className="combat-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Attack Zone &amp; Bash</span>
          <strong className="kpi-value tabular" id="cmb-az-val">
            {cmb
              ? `${az.label || "AZ —"} (${cmb.newbie_protected ? "Newbie Shield" : "Open"})`
              : "AZ —"}
          </strong>
          <span className="kpi-sub tabular" id="cmb-az-rule">
            {cmb
              ? `${az.range_text || "—"} pts · Margin ${cmb.safety_margin || 1.4}x · Bash 3/24h`
              : "±2 AZ · Max 3/24h active"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Combat Techs (r8 / r9 / r10)</span>
          <strong className="kpi-value tabular" id="cmb-tech-val">
            {`L${techs.weapons ?? 0} / L${techs.shield ?? 0} / L${techs.defense ?? 0}`}
          </strong>
          <span className="kpi-sub tabular" id="cmb-tech-sub">
            {cmb
              ? `Spy L${techs.spy ?? 0} · Targeting L${techs.targeting ?? 0} · Scanner L${cmb.galaxy_scanner_level ?? 0}`
              : "Spy L0 · Targeting L0"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Stationed Power (Colony)</span>
          <strong className="kpi-value tabular" id="cmb-power-val">
            {`${fmtInt(Math.round(fltPow))} / ${fmtInt(Math.round(defPow))}`}
          </strong>
          <span className="kpi-sub tabular" id="cmb-cargo-val">
            {`Cargo ${fmtInt(cargoCap)} · Weapons Fac L${cmb?.weapons_factory_level ?? 0}`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Fleet Build Navigation</span>
          <div className="combat-jumps">
            <button
              type="button"
              className="btn btn-secondary btn-sm nav-jump"
              data-action="internalWeaponFactory"
              onClick={() => handleNavJump("internalWeaponFactory")}
            >
              Shipyard
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm nav-jump"
              data-action="internalFleet"
              onClick={() => handleNavJump("internalFleet")}
            >
              Fleet
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm nav-jump"
              data-action="internalGalaxy"
              onClick={() => handleNavJump("internalGalaxy")}
            >
              Galaxy
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-scan-galaxy"
              onClick={handleScanGalaxy}
            >
              Scan Galaxy
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-refresh-combat"
              onClick={handleRefreshCombat}
            >
              Sync Shipyard
            </button>
          </div>
        </div>
      </div>

      <div className="combat-main-grid">
        {/* Left Column: Fleet Build — Shipyard, Defenses & Escape Flight */}
        <article className="hud-window">
          <div className="window-bar">
            <div className="window-title-group">
              <span className="module-code tabular">SHP</span>
              <h2>Shipyard &amp; Defenses</h2>
            </div>
            <div className="tab-group" role="tablist" aria-label="Fleet Build subtabs">
              <button
                type="button"
                className={`tab-btn${subTab === "ships" ? " active" : ""}`}
                data-cmb-tab="ships"
                onClick={() => setUiPref("combatSubTab", "ships")}
              >
                Ships &amp; Build
              </button>
              <button
                type="button"
                className={`tab-btn${subTab === "defense" ? " active" : ""}`}
                data-cmb-tab="defense"
                onClick={() => setUiPref("combatSubTab", "defense")}
              >
                Defense
              </button>
              <button
                type="button"
                className={`tab-btn${subTab === "targets" ? " active" : ""}`}
                id="cmb-targets-tab"
                data-cmb-tab="targets"
                onClick={() => setUiPref("combatSubTab", "targets")}
              >
                {`Targets (${raidTargets.length})`}
              </button>
              <button
                type="button"
                className={`tab-btn${subTab === "escape" ? " active" : ""}`}
                data-cmb-tab="escape"
                onClick={() => setUiPref("combatSubTab", "escape")}
              >
                Escape Flight
              </button>
            </div>
          </div>

          <CombatShipyardPane
            subTab={subTab}
            ships={ships}
            defenses={defenses}
            offersById={offersById}
            shipQtys={shipQtys}
            onChangeShipQty={(uid, value) =>
              setShipQtys((prev) => ({ ...prev, [uid]: value }))
            }
            onBuildShip={handleBuildShip}
            onQueueShip={handleQueueShip}
          />

          <CombatTargetsPane
            hidden={subTab !== "targets"}
            raidTargets={raidTargets}
            onAttackTarget={handleAttackTarget}
          />

          <EscapeFlightPane hidden={subTab !== "escape"} cmb={cmb} />
        </article>

        {/* Right Column: 1:1 Zero-Rapidfire Combat Simulator */}
        <CombatSimulator cmb={cmb} />
      </div>
    </section>
  );
};

export default CombatView;
