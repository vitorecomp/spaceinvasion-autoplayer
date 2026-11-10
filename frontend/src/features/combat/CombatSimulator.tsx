import React, { useCallback, useEffect, useRef, useState } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { useConsoleActions, useSelectedPlanetId } from "../../store";
import { finiteNum, fmtInt } from "../../utils/formatters";
import { SIM_POPULATE_EVENT, SimPopulateTarget } from "./simEvents";

export interface CombatSimulatorProps {
  cmb: Record<string, any> | null;
}

const ATK_KEYS = ["s4", "s6", "s7", "s13", "s1", "s2"] as const;
type AtkKey = (typeof ATK_KEYS)[number];

interface SimInputs {
  atk: Record<AtkKey, string>;
  defD1: string;
  defD2: string;
  defD4: string;
  defS4: string;
  lootTotal: string;
  margin: string;
}

const INITIAL_INPUTS: SimInputs = {
  atk: {
    s4: "10",
    s6: "0",
    s7: "0",
    s13: "0",
    s1: "4",
    s2: "0",
  },
  defD1: "2",
  defD2: "0",
  defD4: "0",
  defS4: "0",
  lootTotal: "20000",
  margin: "1.4",
};

interface SimResultState {
  verdictText: string;
  isUnsafe: boolean;
  ratioText: string;
  atkPwr: string;
  defPwr: string;
  cargo: string;
  plunder: string;
  notes: string;
}

const INITIAL_RESULT: SimResultState = {
  verdictText: "READY",
  isUnsafe: false,
  ratioText: "Configure forces and run 1:1 combat estimation.",
  atkPwr: "—",
  defPwr: "—",
  cargo: "—",
  plunder: "—",
  notes: "No rapidfire: units resolve 1:1 against attacker weapon/shield/defense tech levels.",
};

function ownedAtkMapFrom(cmb: Record<string, any> | null): Record<AtkKey, string> {
  const byKey = new Map<string, number>(
    Array.isArray(cmb?.ships)
      ? cmb!.ships.map((s: any) => [String(s?.key || ""), Number(s?.owned || 0)])
      : []
  );
  return {
    s4: String(byKey.get("s4") || 0),
    s6: String(byKey.get("s6") || 0),
    s7: String(byKey.get("s7") || 0),
    s13: String(byKey.get("s13") || 0),
    s1: String(byKey.get("s1") || 0),
    s2: String(byKey.get("s2") || 0),
  };
}

export const CombatSimulator: React.FC<CombatSimulatorProps> = ({ cmb }) => {
  const selectedPlanetId = useSelectedPlanetId();
  const { apiCommand, showFeedback, getRuntime, setSimPopulatedOnce } = useConsoleActions();
  const [inputs, setInputs] = useState<SimInputs>(INITIAL_INPUTS);
  const [result, setResult] = useState<SimResultState>(INITIAL_RESULT);
  const populatedPlanetRef = useRef<number | null | undefined>(undefined);
  const inputsRef = useRef<SimInputs>(inputs);
  inputsRef.current = inputs;

  const runSimulationWith = useCallback(
    async (draft: SimInputs) => {
      const atk: Record<string, number> = {};
      for (const key of ATK_KEYS) {
        const val = Math.max(0, Math.round(finiteNum(draft.atk[key], 0)));
        if (val > 0) atk[key] = val;
      }
      const defShips: Record<string, number> = {};
      const s4 = Math.max(0, Math.round(finiteNum(draft.defS4, 0)));
      if (s4 > 0) defShips.s4 = s4;

      const defDefs: Record<string, number> = {};
      const d1 = Math.max(0, Math.round(finiteNum(draft.defD1, 0)));
      const d2 = Math.max(0, Math.round(finiteNum(draft.defD2, 0)));
      const d4 = Math.max(0, Math.round(finiteNum(draft.defD4, 0)));
      if (d1 > 0) defDefs.d1 = d1;
      if (d2 > 0) defDefs.d2 = d2;
      if (d4 > 0) defDefs.d4 = d4;

      const totalLoot = Math.max(0, Math.round(finiteNum(draft.lootTotal, 0)));
      const split = Math.floor(totalLoot / 4);
      const margin = Math.max(1.0, finiteNum(draft.margin, 1.4));

      try {
        const res = await apiCommand({
          command: "simulate_combat",
          attacker_ships: atk,
          defender_ships: defShips,
          defender_defense: defDefs,
          target_resources: {
            pig_iron: split,
            metal: split,
            kryptonite: split,
            spice: totalLoot - split * 3,
          },
          safety_margin: margin,
        });
        const sim = res.simulation || {};
        const safe = Boolean(sim.safe_to_attack);
        const winRatioNum = finiteNum(sim.win_ratio, null);
        const ratioStr =
          sim.win_ratio === Infinity || sim.win_ratio === "Infinity"
            ? "INF"
            : winRatioNum !== null
              ? winRatioNum.toFixed(2)
              : "0.00";
        setResult({
          verdictText: safe ? "SAFE TO RAID" : "UNSAFE / HOLD",
          isUnsafe: !safe,
          ratioText: `Power Ratio: ${ratioStr}x (Threshold ${finiteNum(sim.safety_margin, margin)}x)`,
          atkPwr: fmtInt(Math.round(finiteNum(sim.attacker_power, 0))),
          defPwr: fmtInt(Math.round(finiteNum(sim.defender_power, 0))),
          cargo: fmtInt(finiteNum(sim.cargo_capacity, 0)),
          plunder: `${fmtInt(finiteNum(sim.total_plunder, 0))} (${fmtInt(Math.round(finiteNum(sim.loot_spice_equivalent, 0)))} SpEq)`,
          notes: String(sim.notes || "Simulation complete."),
        });
      } catch (err: any) {
        showFeedback(`Simulation failed: ${err.message}`, feedbackKindForError(err));
      }
    },
    [apiCommand, showFeedback]
  );

  // Reset auto-populate guard when selected planet changes
  useEffect(() => {
    if (populatedPlanetRef.current !== undefined && populatedPlanetRef.current !== selectedPlanetId) {
      populatedPlanetRef.current = undefined;
    }
  }, [selectedPlanetId]);

  // Auto-populate attacker ships once per planet when stationed ships arrive
  useEffect(() => {
    const ships = Array.isArray(cmb?.ships) ? cmb!.ships : [];
    const hasOwned = ships.some((s: any) => Number(s?.owned) > 0);
    if (
      hasOwned &&
      populatedPlanetRef.current === undefined &&
      !getRuntime().simPopulatedOnce
    ) {
      populatedPlanetRef.current = selectedPlanetId;
      setSimPopulatedOnce(true);
      setInputs((prev) => ({
        ...prev,
        atk: ownedAtkMapFrom(cmb),
      }));
    }
  }, [cmb, getRuntime, selectedPlanetId, setSimPopulatedOnce]);

  // Listen for cross-view / target-row "Sim" button populate events
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onPopulate = (ev: Event) => {
      const detail = (ev as CustomEvent<SimPopulateTarget>).detail || {};
      const def = detail.defense || {};
      const flt = detail.fleet || {};
      const nextDraft: SimInputs = {
        ...inputsRef.current,
        atk: ownedAtkMapFrom(cmb),
        defD1: String(Math.max(0, Math.round(finiteNum(def.d1, 0)))),
        defD2: String(Math.max(0, Math.round(finiteNum(def.d2, 0)))),
        defD4: String(Math.max(0, Math.round(finiteNum(def.d4, 0)))),
        defS4: String(Math.max(0, Math.round(finiteNum(flt.s4, 0)))),
        lootTotal: String(Math.max(0, Math.round(finiteNum(detail.totalLoot, 0)))),
      };
      setInputs(nextDraft);
      runSimulationWith(nextDraft);
    };
    window.addEventListener(SIM_POPULATE_EVENT, onPopulate);
    return () => window.removeEventListener(SIM_POPULATE_EVENT, onPopulate);
  }, [cmb, runSimulationWith]);

  const handleUseOwned = () => {
    const nextDraft: SimInputs = {
      ...inputs,
      atk: ownedAtkMapFrom(cmb),
    };
    setInputs(nextDraft);
    runSimulationWith(nextDraft);
  };

  const setAtkField = (key: AtkKey, val: string) => {
    setInputs((prev) => ({
      ...prev,
      atk: { ...prev.atk, [key]: val },
    }));
  };

  return (
    <article className="hud-window">
      <div className="window-bar">
        <div className="window-title-group">
          <span className="module-code tabular">SIM</span>
          <h2>1:1 Zero-Rapidfire Raid Simulator</h2>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            id="btn-sim-use-owned"
            onClick={handleUseOwned}
          >
            Use Owned Fleet
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            id="btn-run-sim"
            onClick={() => runSimulationWith(inputs)}
          >
            Simulate Raid
          </button>
        </div>
      </div>

      <div className="sim-body">
        <div className="sim-inputs-grid">
          <div className="sim-group">
            <h3>Attacker Ships</h3>
            <div className="sim-fields">
              <label>
                Small Fighter (s4){" "}
                <input
                  type="number"
                  id="sim-atk-s4"
                  min={0}
                  value={inputs.atk.s4}
                  onChange={(e) => setAtkField("s4", e.target.value)}
                />
              </label>
              <label>
                Starcruiser (s6){" "}
                <input
                  type="number"
                  id="sim-atk-s6"
                  min={0}
                  value={inputs.atk.s6}
                  onChange={(e) => setAtkField("s6", e.target.value)}
                />
              </label>
              <label>
                Phoenix (s7){" "}
                <input
                  type="number"
                  id="sim-atk-s7"
                  min={0}
                  value={inputs.atk.s7}
                  onChange={(e) => setAtkField("s7", e.target.value)}
                />
              </label>
              <label>
                Battleship (s13){" "}
                <input
                  type="number"
                  id="sim-atk-s13"
                  min={0}
                  value={inputs.atk.s13}
                  onChange={(e) => setAtkField("s13", e.target.value)}
                />
              </label>
              <label>
                Small Transp. (s1){" "}
                <input
                  type="number"
                  id="sim-atk-s1"
                  min={0}
                  value={inputs.atk.s1}
                  onChange={(e) => setAtkField("s1", e.target.value)}
                />
              </label>
              <label>
                Large Transp. (s2){" "}
                <input
                  type="number"
                  id="sim-atk-s2"
                  min={0}
                  value={inputs.atk.s2}
                  onChange={(e) => setAtkField("s2", e.target.value)}
                />
              </label>
            </div>
          </div>

          <div className="sim-group">
            <h3>Defender Fleet &amp; Turrets</h3>
            <div className="sim-fields">
              <label>
                Missile Turret (d1){" "}
                <input
                  type="number"
                  id="sim-def-d1"
                  min={0}
                  value={inputs.defD1}
                  onChange={(e) => setInputs((prev) => ({ ...prev, defD1: e.target.value }))}
                />
              </label>
              <label>
                Laser Battery (d2){" "}
                <input
                  type="number"
                  id="sim-def-d2"
                  min={0}
                  value={inputs.defD2}
                  onChange={(e) => setInputs((prev) => ({ ...prev, defD2: e.target.value }))}
                />
              </label>
              <label>
                Rocket Dome (d4){" "}
                <input
                  type="number"
                  id="sim-def-d4"
                  min={0}
                  value={inputs.defD4}
                  onChange={(e) => setInputs((prev) => ({ ...prev, defD4: e.target.value }))}
                />
              </label>
              <label>
                Small Fighter (s4){" "}
                <input
                  type="number"
                  id="sim-def-s4"
                  min={0}
                  value={inputs.defS4}
                  onChange={(e) => setInputs((prev) => ({ ...prev, defS4: e.target.value }))}
                />
              </label>
              <label>
                Target Total Loot{" "}
                <input
                  type="number"
                  id="sim-loot-total"
                  min={0}
                  step={1000}
                  value={inputs.lootTotal}
                  onChange={(e) => setInputs((prev) => ({ ...prev, lootTotal: e.target.value }))}
                />
              </label>
              <label>
                Safety Margin{" "}
                <input
                  type="number"
                  id="sim-margin"
                  min={1.0}
                  max={5.0}
                  step={0.1}
                  value={inputs.margin}
                  onChange={(e) => setInputs((prev) => ({ ...prev, margin: e.target.value }))}
                />
              </label>
            </div>
          </div>
        </div>

        <div className="sim-result-card" id="sim-result-box">
          <div className="sim-verdict-row" id="sim-verdict">
            <span
              className={`run-badge tabular${result.isUnsafe ? " is-paused" : ""}`}
              id="sim-verdict-badge"
            >
              {result.verdictText}
            </span>
            <span className="tabular" id="sim-ratio-text">
              {result.ratioText}
            </span>
          </div>
          <div className="sim-metrics-grid tabular" id="sim-metrics-grid">
            <div>
              <span>Attacker Power</span>
              <strong id="sim-atk-pwr">{result.atkPwr}</strong>
            </div>
            <div>
              <span>Defender Power</span>
              <strong id="sim-def-pwr">{result.defPwr}</strong>
            </div>
            <div>
              <span>Cargo Capacity</span>
              <strong id="sim-cargo">{result.cargo}</strong>
            </div>
            <div>
              <span>Est. 50% Plunder</span>
              <strong id="sim-plunder">{result.plunder}</strong>
            </div>
          </div>
          <p className="action-sub" id="sim-notes">
            {result.notes}
          </p>
        </div>
      </div>
    </article>
  );
};

export default CombatSimulator;
