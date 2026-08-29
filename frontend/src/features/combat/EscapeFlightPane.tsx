import React, { useState } from "react";
import { useConsole } from "../../context/ConsoleContext";

export interface EscapeFlightPaneProps {
  hidden?: boolean;
  cmb: Record<string, any> | null;
}

const DEFAULT_OUTPUT = 'Click "Pack Escape Flight" to calculate optimal cargo distribution.';

export const EscapeFlightPane: React.FC<EscapeFlightPaneProps> = ({ hidden = false, cmb }) => {
  const { activePlanet, apiCommand } = useConsole();
  const [targetCoords, setTargetCoords] = useState<string>("");
  const [customOutput, setCustomOutput] = useState<string | null>(null);

  const displayedOutput =
    customOutput !== null
      ? customOutput
      : cmb?.escape_plan
        ? JSON.stringify(cmb.escape_plan, null, 2)
        : DEFAULT_OUTPUT;

  const handleCalcFleetsave = async () => {
    const trimmed = targetCoords.trim();
    try {
      const res = await apiCommand({
        command: "fleetsave_plan",
        planet_id: activePlanet?.planet_id,
        target_coords: trimmed || undefined,
      });
      setCustomOutput(JSON.stringify(res.escape_plan, null, 2));
    } catch (err: any) {
      setCustomOutput(`Error: ${err.message}`);
    }
  };

  return (
    <div className="combat-subpane fleet-pane" id="cmb-pane-escape" hidden={hidden}>
      <p className="muted">
        Computes zero-scanner-visible Escape Flight resource packing from current colony reserves.
      </p>
      <div className="fleetsave-form">
        <div className="control-field">
          <label htmlFor="fs-target">Destination Coords (blank = next colony)</label>
          <input
            type="text"
            id="fs-target"
            placeholder="e.g. 02:381:05"
            value={targetCoords}
            onChange={(e) => setTargetCoords(e.target.value)}
          />
        </div>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          id="btn-calc-fleetsave"
          onClick={handleCalcFleetsave}
        >
          Pack Escape Flight
        </button>
      </div>
      <pre id="fleetsave-output" className="console-box">
        {displayedOutput}
      </pre>
    </div>
  );
};

export default EscapeFlightPane;
