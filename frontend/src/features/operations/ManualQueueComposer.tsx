import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  flushSync } from "react-dom";
import { useActivePlanet,
  useConsoleActions,
  useDashboard,
  useUiPrefs,
} from "../../store";
import type { PlanetData } from "../../types/api";
import { finiteNum } from "../../utils/formatters";
import {
  MQ_BUILDINGS_CATALOG,
  MQ_RESEARCH_CATALOG,
  MQ_SHIPS_CATALOG,
} from "./planActions";

function resolveDefaultTargetId(kind: string, prevTargetId: string): string {
  const normKind = String(kind || "BUILD").toUpperCase();
  if (
    normKind === "BUILD" ||
    normKind === "DEMOLISH_BUILDING" ||
    normKind === "THROTTLE_BUILDING"
  ) {
    const items =
      normKind === "THROTTLE_BUILDING"
        ? MQ_BUILDINGS_CATALOG.slice(0, 6)
        : MQ_BUILDINGS_CATALOG;
    if (items.some((b) => String(b.id) === prevTargetId)) {
      return prevTargetId;
    }
    if (normKind === "DEMOLISH_BUILDING") return "6";
    if (normKind === "THROTTLE_BUILDING") return "2";
    return "1";
  }
  if (normKind === "RESEARCH") {
    return MQ_RESEARCH_CATALOG.some((r) => String(r.id) === prevTargetId) ? prevTargetId : "1";
  }
  if (normKind === "BUILD_SHIP") {
    return MQ_SHIPS_CATALOG.some((s) => String(s.id) === prevTargetId) ? prevTargetId : "1";
  }
  return prevTargetId || "1";
}

function computeDefaultLevelValue(
  kind: string,
  targetId: string,
  chosenPlanet: PlanetData | null,
  researches: Record<string, number>
): string {
  const normKind = String(kind || "BUILD").toUpperCase();
  const bMap = chosenPlanet?.buildings || {};
  if (normKind === "BUILD") {
    const bid = Number(targetId || 1);
    const curL = finiteNum(bMap[`b${bid}`], 0);
    return String(curL + 1);
  }
  if (normKind === "DEMOLISH_BUILDING") {
    const bid = Number(targetId || 6);
    const curL = finiteNum(bMap[`b${bid}`], 0);
    return String(Math.max(0, curL - 1));
  }
  if (normKind === "THROTTLE_BUILDING") {
    return "100";
  }
  if (normKind === "RESEARCH") {
    const rid = Number(targetId || 1);
    const curL = finiteNum(researches[`r${rid}`], 0);
    return String(curL + 1);
  }
  if (normKind === "BUILD_SHIP" || normKind === "SPY" || normKind === "RECYCLE") {
    return "1";
  }
  return "";
}

export const ManualQueueComposer: React.FC = () => {
  const dashboard = useDashboard();
  const activePlanet = useActivePlanet();
  const uiPrefs = useUiPrefs();
  const { showFeedback, enqueueManualCommand } = useConsoleActions();

  const planets = useMemo(() => dashboard?.state?.planets || [], [dashboard?.state?.planets]);
  const researches = useMemo(
    () => dashboard?.state?.researches || {},
    [dashboard?.state?.researches]
  );

  const presetKind = String(uiPrefs.mqKind || "BUILD").toUpperCase();
  const presetSeq = Number(uiPrefs.mqPresetSeq || 0);

  const [kind, setKind] = useState<string>(presetKind);
  const [planetId, setPlanetId] = useState<number>(() => activePlanet?.planet_id ?? 0);
  const [targetId, setTargetId] = useState<string>(() => resolveDefaultTargetId(presetKind, "1"));
  const [lastPresetSeq, setLastPresetSeq] = useState<number>(presetSeq);
  const [hydratedPlanetOnce, setHydratedPlanetOnce] = useState<boolean>(false);

  const formRef = useRef<HTMLFormElement | null>(null);
  const planetSelectRef = useRef<HTMLSelectElement | null>(null);
  const kindSelectRef = useRef<HTMLSelectElement | null>(null);
  const targetSelectRef = useRef<HTMLSelectElement | null>(null);
  const coordsInputRef = useRef<HTMLInputElement | null>(null);
  const levelInputRef = useRef<HTMLInputElement | null>(null);
  const prioSelectRef = useRef<HTMLSelectElement | null>(null);
  const posSelectRef = useRef<HTMLSelectElement | null>(null);
  const noteInputRef = useRef<HTMLInputElement | null>(null);

  const pendingForceLevelSyncRef = useRef<boolean>(true);
  const handledEventsRef = useRef<WeakSet<Event>>(new WeakSet());

  // Synchronously adopt Dashboard "+ Queue" preset jumps during render
  if (presetSeq !== lastPresetSeq) {
    setLastPresetSeq(presetSeq);
    const nextTarget = resolveDefaultTargetId(presetKind, targetId);
    setKind(presetKind);
    setTargetId(nextTarget);
    if (activePlanet?.planet_id) {
      setPlanetId(activePlanet.planet_id);
    }
    pendingForceLevelSyncRef.current = true;
  }

  // Hydrate initial planetId once planets load
  if (planets.length > 0 && (!hydratedPlanetOnce || !planets.some((p) => p.planet_id === planetId))) {
    const fallbackPid = activePlanet?.planet_id ?? planets[0].planet_id;
    if (planetId !== fallbackPid || !hydratedPlanetOnce) {
      setHydratedPlanetOnce(true);
      setPlanetId(fallbackPid);
      pendingForceLevelSyncRef.current = true;
    }
  }

  const chosenPlanet = useMemo(() => {
    if (planets.length === 0) return activePlanet;
    return planets.find((p) => p.planet_id === planetId) || activePlanet || planets[0] || null;
  }, [activePlanet, planetId, planets]);

  const bMap = chosenPlanet?.buildings || {};
  const sMap = chosenPlanet?.ships || {};

  const raidCoordsList = useMemo(() => {
    const raw = dashboard?.state?.raid_targets;
    if (!Array.isArray(raw)) return [];
    const seen = new Set<string>();
    for (const rt of raw) {
      const c = rt?.coords || rt?.target_coords;
      if (c) seen.add(String(c));
      if (seen.size >= 40) break;
    }
    return Array.from(seen);
  }, [dashboard?.state?.raid_targets]);

  const normKind = String(kind || "BUILD").toUpperCase();
  const isBuildingKind =
    normKind === "BUILD" ||
    normKind === "DEMOLISH_BUILDING" ||
    normKind === "THROTTLE_BUILDING";
  const isResearchKind = normKind === "RESEARCH";
  const isShipKind = normKind === "BUILD_SHIP";
  const isFleetCoordsKind =
    normKind === "SPY" ||
    normKind === "ATTACK" ||
    normKind === "RECYCLE" ||
    normKind === "COLONIZE";
  const isScanKind = normKind === "SCAN_GALAXY";

  const showUnitWrap = isBuildingKind || isResearchKind || isShipKind;
  const showCoordsWrap = isFleetCoordsKind || isScanKind;
  const showLevelWrap =
    isBuildingKind ||
    isResearchKind ||
    isShipKind ||
    normKind === "SPY" ||
    normKind === "RECYCLE";

  const unitLabel = isBuildingKind
    ? "Building"
    : isResearchKind
      ? "Research"
      : isShipKind
        ? "Ship Class"
        : "Target Unit";

  const coordsLabel = isScanKind ? "System (GG:SSS)" : "Target Coords";
  const defSys = chosenPlanet?.coords ? chosenPlanet.coords.slice(0, 6) : "02:380";
  const coordsPlaceholder = isScanKind ? defSys : "02:381:12";

  let levelLabel = "Goal Lvl";
  let levelMin = "1";
  let levelMax = "200";
  let levelStep = "1";
  if (normKind === "DEMOLISH_BUILDING") {
    levelLabel = "Down To Lvl";
    levelMin = "0";
    levelMax = "200";
  } else if (normKind === "THROTTLE_BUILDING") {
    levelLabel = "Rate %";
    levelMin = "0";
    levelMax = "100";
    levelStep = "10";
  } else if (isShipKind) {
    levelLabel = "Quantity";
    levelMin = "1";
    levelMax = "99999";
  } else if (normKind === "SPY") {
    levelLabel = "Probes";
    levelMin = "1";
    levelMax = "9999";
  } else if (normKind === "RECYCLE") {
    levelLabel = "Recyclers";
    levelMin = "1";
    levelMax = "9999";
  }

  const unitOptions = useMemo(() => {
    if (isBuildingKind) {
      const items =
        normKind === "THROTTLE_BUILDING"
          ? MQ_BUILDINGS_CATALOG.slice(0, 6)
          : MQ_BUILDINGS_CATALOG;
      return items.map((b) => ({
        value: String(b.id),
        label: `${b.name} (L${finiteNum(bMap[`b${b.id}`], 0)})`,
      }));
    }
    if (isResearchKind) {
      return MQ_RESEARCH_CATALOG.map((r) => ({
        value: String(r.id),
        label: `${r.name} (L${finiteNum(researches[`r${r.id}`], 0)})`,
      }));
    }
    if (isShipKind) {
      return MQ_SHIPS_CATALOG.map((s) => ({
        value: String(s.id),
        label: `${s.name} (${finiteNum(sMap[s.key], 0)} owned)`,
      }));
    }
    return [];
  }, [bMap, isBuildingKind, isResearchKind, isShipKind, normKind, researches, sMap]);

  const defaultLevelStr = useMemo(
    () => computeDefaultLevelValue(normKind, targetId, chosenPlanet, researches),
    [chosenPlanet, normKind, researches, targetId]
  );

  // Sync uncontrolled #mq-level / #mq-coords default values when kind/target/planet/preset updates
  useEffect(() => {
    const lvlInp = levelInputRef.current;
    if (lvlInp) {
      const activeEl = typeof document !== "undefined" ? document.activeElement : null;
      const isEditingLevel = activeEl === lvlInp;
      if (pendingForceLevelSyncRef.current || (!isEditingLevel && !lvlInp.value)) {
        lvlInp.value = defaultLevelStr;
      }
    }
    if (normKind === "SCAN_GALAXY" && coordsInputRef.current && pendingForceLevelSyncRef.current) {
      if (!coordsInputRef.current.value) {
        coordsInputRef.current.value = defSys;
      }
    }
    pendingForceLevelSyncRef.current = false;
  }, [defaultLevelStr, defSys, normKind, planetId, presetSeq, targetId]);

  const applyKindChange = useCallback(
    (rawKind: string) => {
      const nextKind = String(rawKind || "BUILD").toUpperCase();
      const nextTarget = resolveDefaultTargetId(nextKind, targetSelectRef.current?.value || targetId);
      setKind(nextKind);
      setTargetId(nextTarget);
      if (levelInputRef.current) {
        levelInputRef.current.value = computeDefaultLevelValue(
          nextKind,
          nextTarget,
          chosenPlanet,
          researches
        );
      }
      if (nextKind === "SCAN_GALAXY" && coordsInputRef.current && !coordsInputRef.current.value) {
        coordsInputRef.current.value = defSys;
      }
    },
    [chosenPlanet, defSys, researches, targetId]
  );

  const applyPlanetChange = useCallback(
    (rawPid: string) => {
      const nextPid = Math.max(0, Math.round(finiteNum(rawPid, 0)));
      setPlanetId(nextPid);
      const nextPlanet = planets.find((p) => p.planet_id === nextPid) || chosenPlanet;
      if (levelInputRef.current) {
        levelInputRef.current.value = computeDefaultLevelValue(
          normKind,
          targetId,
          nextPlanet,
          researches
        );
      }
    },
    [chosenPlanet, normKind, planets, researches, targetId]
  );

  const applyTargetChange = useCallback(
    (rawTid: string) => {
      const nextTid = String(rawTid || "1");
      setTargetId(nextTid);
      if (levelInputRef.current) {
        levelInputRef.current.value = computeDefaultLevelValue(
          normKind,
          nextTid,
          chosenPlanet,
          researches
        );
      }
    },
    [chosenPlanet, normKind, researches]
  );

  const submitForm = useCallback(async () => {
    const curKind = String(kindSelectRef.current?.value || normKind || "BUILD").toUpperCase();
    const curPriority = String(prioSelectRef.current?.value || "P1").toUpperCase();
    const curPosition = String(posSelectRef.current?.value || "top").toLowerCase();
    const curPlanetId = Math.max(
      0,
      Math.round(finiteNum(planetSelectRef.current?.value ?? planetId, 0))
    );
    const curNote = String(noteInputRef.current?.value || "").trim();

    const payload: Record<string, any> = {
      kind: curKind,
      priority: curPriority,
      position: curPosition,
      planet_id: curPlanetId,
      note: curNote,
    };

    if (
      ["BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING"].includes(
        curKind
      )
    ) {
      payload.target_id = Math.max(
        1,
        Math.round(finiteNum(targetSelectRef.current?.value ?? targetId, 1))
      );
      const rawLvl = String(levelInputRef.current?.value ?? "").trim();
      if (rawLvl !== "") {
        payload.target_level = Math.max(0, Math.round(finiteNum(rawLvl, 1)));
      }
    } else if (["SPY", "ATTACK", "RECYCLE", "COLONIZE", "SCAN_GALAXY"].includes(curKind)) {
      const coords = String(coordsInputRef.current?.value || "").trim();
      if (!coords) {
        showFeedback("Enter target coordinates (e.g. 02:381:12) before queuing.", "warn");
        coordsInputRef.current?.focus?.();
        return;
      }
      payload.target_coords = coords;
      if (curKind === "SPY" || curKind === "RECYCLE") {
        payload.target_level = Math.max(1, Math.round(finiteNum(levelInputRef.current?.value, 1)));
      }
    }

    const res = await enqueueManualCommand(payload);
    if (res && noteInputRef.current) {
      noteInputRef.current.value = "";
    }
  }, [enqueueManualCommand, normKind, planetId, showFeedback, targetId]);

  // Native change/submit listeners ensure even non-bubbling synthetic events in jsdom work synchronously
  useEffect(() => {
    const kSel = kindSelectRef.current;
    const pSel = planetSelectRef.current;
    const tSel = targetSelectRef.current;
    const form = formRef.current;

    const onKindNative = (ev: Event) => {
      if (handledEventsRef.current.has(ev)) return;
      handledEventsRef.current.add(ev);
      if (kSel) flushSync(() => applyKindChange(kSel.value));
    };
    const onPlanetNative = (ev: Event) => {
      if (handledEventsRef.current.has(ev)) return;
      handledEventsRef.current.add(ev);
      if (pSel) flushSync(() => applyPlanetChange(pSel.value));
    };
    const onTargetNative = (ev: Event) => {
      if (handledEventsRef.current.has(ev)) return;
      handledEventsRef.current.add(ev);
      if (tSel) flushSync(() => applyTargetChange(tSel.value));
    };
    const onFormNative = (ev: Event) => {
      ev.preventDefault?.();
      if (handledEventsRef.current.has(ev)) return;
      handledEventsRef.current.add(ev);
      void submitForm();
    };

    kSel?.addEventListener("change", onKindNative);
    pSel?.addEventListener("change", onPlanetNative);
    tSel?.addEventListener("change", onTargetNative);
    form?.addEventListener("submit", onFormNative);

    return () => {
      kSel?.removeEventListener("change", onKindNative);
      pSel?.removeEventListener("change", onPlanetNative);
      tSel?.removeEventListener("change", onTargetNative);
      form?.removeEventListener("submit", onFormNative);
    };
  }, [applyKindChange, applyPlanetChange, applyTargetChange, submitForm]);

  return (
    <form
      ref={formRef}
      className="mq-composer-bar"
      id="mq-composer-form"
      aria-label="Enqueue prioritized command"
      onSubmit={(e) => {
        e.preventDefault();
        if (handledEventsRef.current.has(e.nativeEvent)) return;
        handledEventsRef.current.add(e.nativeEvent);
        void submitForm();
      }}
    >
      <div className="mq-field">
        <label htmlFor="mq-planet">Colony</label>
        <select
          ref={planetSelectRef}
          id="mq-planet"
          value={planets.length > 0 ? String(chosenPlanet?.planet_id ?? planetId) : "0"}
          onChange={(e) => {
            handledEventsRef.current.add(e.nativeEvent);
            applyPlanetChange(e.target.value);
          }}
        >
          {planets.length === 0 ? (
            <option value="0">Active Colony</option>
          ) : (
            planets.map((p) => (
              <option key={p.planet_id} value={String(p.planet_id)}>
                {`[${p.coords}] ${p.name || "Colony"}`}
              </option>
            ))
          )}
        </select>
      </div>

      <div className="mq-field">
        <label htmlFor="mq-kind">Command</label>
        <select
          ref={kindSelectRef}
          id="mq-kind"
          value={normKind}
          onChange={(e) => {
            handledEventsRef.current.add(e.nativeEvent);
            applyKindChange(e.target.value);
          }}
        >
          <option value="BUILD">Build / Upgrade Building</option>
          <option value="DEMOLISH_BUILDING">Demolish Building</option>
          <option value="RESEARCH">Start Research</option>
          <option value="BUILD_SHIP">Build Ships</option>
          <option value="THROTTLE_BUILDING">Throttle Building %</option>
          <option value="SPY">Spy Target</option>
          <option value="ATTACK">Attack / Raid Target</option>
          <option value="RECYCLE">Recycle Debris</option>
          <option value="COLONIZE">Colonize Free Slot</option>
          <option value="SCAN_GALAXY">Scan Galaxy System</option>
          <option value="CLAIM_TUTORIAL">Claim Tutorial Reward</option>
        </select>
      </div>

      <div className="mq-field mq-field-wide" id="mq-unit-wrap" hidden={!showUnitWrap}>
        <label htmlFor="mq-target-id" id="mq-unit-label">
          {unitLabel}
        </label>
        <select
          ref={targetSelectRef}
          id="mq-target-id"
          value={targetId}
          onChange={(e) => {
            handledEventsRef.current.add(e.nativeEvent);
            applyTargetChange(e.target.value);
          }}
        >
          {unitOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <div className="mq-field" id="mq-coords-wrap" hidden={!showCoordsWrap}>
        <label htmlFor="mq-coords" id="mq-coords-label">
          {coordsLabel}
        </label>
        <input
          ref={coordsInputRef}
          type="text"
          id="mq-coords"
          list="mq-coords-datalist"
          placeholder={coordsPlaceholder}
          defaultValue=""
          autoComplete="off"
        />
        <datalist id="mq-coords-datalist">
          {raidCoordsList.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
      </div>

      <div className="mq-field mq-field-num" id="mq-level-wrap" hidden={!showLevelWrap}>
        <label htmlFor="mq-level" id="mq-level-label">
          {levelLabel}
        </label>
        <input
          ref={levelInputRef}
          type="number"
          id="mq-level"
          min={levelMin}
          max={levelMax}
          step={levelStep}
          defaultValue={defaultLevelStr}
          placeholder="Auto"
        />
      </div>

      <div className="mq-field mq-field-prio">
        <label htmlFor="mq-priority">Priority</label>
        <select ref={prioSelectRef} id="mq-priority" defaultValue="P1">
          <option value="P0">P0 · Immediate</option>
          <option value="P1">P1 · High</option>
          <option value="P2">P2 · Normal</option>
          <option value="P3">P3 · Low</option>
        </select>
      </div>

      <div className="mq-field mq-field-pos">
        <label htmlFor="mq-position">Order</label>
        <select ref={posSelectRef} id="mq-position" defaultValue="top">
          <option value="top">Top of Band</option>
          <option value="bottom">End of Band</option>
        </select>
      </div>

      <div className="mq-field mq-field-note">
        <label htmlFor="mq-note">Note</label>
        <input
          ref={noteInputRef}
          type="text"
          id="mq-note"
          maxLength={140}
          defaultValue=""
          placeholder="Optional note"
        />
      </div>

      <div className="mq-submit-wrap">
        <button type="submit" className="btn btn-primary btn-sm" id="btn-mq-submit">
          + Enqueue
        </button>
      </div>
    </form>
  );
};

export default ManualQueueComposer;
