"""Blast furnace (``b2``) smelting span and live-toggle transition tracker."""

from __future__ import annotations

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.ai._sim_types import SimProblemData


class SmeltingTracker:
    """Tracks cumulative Metal smelted, active smelting spans, and Blast furnace toggle events."""

    def __init__(self, data: SimProblemData) -> None:
        self._data = data
        self.metal_smelted: float = 0.0
        self.smelting_spans: list[list[float]] = []
        self.smelting_events: list[dict[str, object]] = []
        self.initialized: bool = False
        self.last_recorded_smelt: bool = False
        self.initial_smelting_target: bool = False
        self.initial_toggle_needed: bool = False
        self.initial_planets_needing_toggle: list[dict[str, object]] = []

    def reset(self) -> None:
        """Reset all smelting metrics and event logs for a fresh rollout."""
        self.metal_smelted = 0.0
        self.smelting_spans = []
        self.smelting_events = []
        self.initialized = False
        self.last_recorded_smelt = False
        self.initial_smelting_target = False
        self.initial_toggle_needed = False
        self.initial_planets_needing_toggle = []

    def accrue(self, clock: float, elapsed: float, furnace_metal: float) -> None:
        """Accrue smelted Metal and extend or append the active smelting time span."""
        self.metal_smelted += furnace_metal * elapsed
        if self.smelting_spans and self.smelting_spans[-1][1] == clock:
            self.smelting_spans[-1][1] = clock + elapsed
        else:
            self.smelting_spans.append([clock, clock + elapsed])

    def _format_step_label(self, step_id: int) -> str:
        data = self._data
        if 0 <= step_id < len(data.steps):
            st = data.steps[step_id]
            p_idx = coerce_int(st.get("planet"), -1)
            where = f" on {data.planets[p_idx]['coords']}" if p_idx >= 0 else ""
            return f"{st['name']} L{coerce_int(st.get('level_to'), 0)}{where}"
        return ""

    def _record_initial_transition(self, effective_smelt: bool, reserved: int) -> None:
        data = self._data
        self.initialized = True
        self.last_recorded_smelt = effective_smelt
        self.initial_smelting_target = effective_smelt
        target_pct = 100 if effective_smelt else 0
        needing_toggle: list[dict[str, object]] = [
            {
                "planet_id": coerce_int(row.get("planet_id"), 0),
                "coords": str(row.get("coords") or ""),
                "name": str(row.get("name") or ""),
                "b2_level": coerce_int(row.get("b2_level"), 0),
                "from_pct": coerce_int(row.get("initial_pct"), 0),
                "to_pct": target_pct,
            }
            for row in data.initial_b2_capable_planets
            if coerce_int(row.get("initial_pct"), 0) != target_pct
        ]
        self.initial_planets_needing_toggle = needing_toggle
        self.initial_toggle_needed = bool(needing_toggle)
        if self.smelting_events and bool(self.smelting_events[-1].get("is_initial")):
            self.smelting_events.pop()
        if not needing_toggle:
            return
        init_bf = data.initial_blast_furnace
        live_mode = str(init_bf.get("mode") or "none")
        live_desc = (
            f"{live_mode.upper()} "
            f"({coerce_int(init_bf.get('enabled_count'), 0)}/{coerce_int(init_bf.get('total_b2_planets'), 0)} ON)"
        )
        step_lbl = self._format_step_label(reserved)
        if effective_smelt:
            reason = (
                f"Initial live b2 {live_desc} -> ENABLE 100%: Metal needed for {step_lbl}"
                if step_lbl
                else f"Initial live b2 {live_desc} -> ENABLE 100%"
            )
        elif step_lbl:
            reason = f"Initial live b2 {live_desc} -> DISABLE 0%: Metal sufficient for {step_lbl}"
        else:
            reason = f"Initial live b2 {live_desc} -> DISABLE 0%: No waiting step requires Metal"
        self.smelting_events.append(
            {
                "time_hours": 0.0,
                "action": "enable" if effective_smelt else "disable",
                "enabled": effective_smelt,
                "target_pct": target_pct,
                "from_mode": live_mode,
                "from_enabled": bool(init_bf.get("any_enabled", False)),
                "is_initial": True,
                "reserved_step_id": int(reserved),
                "affected_planets": needing_toggle,
                "reason": reason,
            }
        )

    def _record_runtime_transition(self, clock: float, effective_smelt: bool, reserved: int) -> None:
        prev_enabled = self.last_recorded_smelt
        self.last_recorded_smelt = effective_smelt
        target_pct = 100 if effective_smelt else 0
        if (
            self.smelting_events
            and not bool(self.smelting_events[-1].get("is_initial"))
            and abs(coerce_finite_float(self.smelting_events[-1].get("time_hours"), 0.0) - clock) <= 1e-12
        ):
            if bool(self.smelting_events[-1].get("from_enabled")) == effective_smelt:
                self.smelting_events.pop()
                return
            self.smelting_events.pop()
        step_lbl = self._format_step_label(reserved)
        if effective_smelt:
            reason = f"ENABLE 100%: Metal shortfall for {step_lbl}" if step_lbl else "ENABLE 100%: Metal required"
        elif step_lbl:
            reason = f"DISABLE 0%: Metal satisfied for {step_lbl}"
        else:
            reason = "DISABLE 0%: Queue complete or no waiting Metal shortfall"
        self.smelting_events.append(
            {
                "time_hours": float(clock),
                "action": "enable" if effective_smelt else "disable",
                "enabled": effective_smelt,
                "target_pct": target_pct,
                "from_mode": "on" if prev_enabled else "off",
                "from_enabled": prev_enabled,
                "is_initial": False,
                "reserved_step_id": int(reserved),
                "affected_planets": [],
                "reason": reason,
            }
        )

    def record_transition(self, clock: float, smelting: bool, b2_founded_count: int, reserved: int) -> None:
        """Record an initial live toggle or mid-rollout Blast furnace state transition."""
        effective_smelt = bool(smelting) and b2_founded_count > 0
        if (not self.initialized) or (
            clock <= 1e-12 and not any(not bool(ev.get("is_initial")) for ev in self.smelting_events)
        ):
            self._record_initial_transition(effective_smelt, reserved)
            return
        if effective_smelt != self.last_recorded_smelt:
            self._record_runtime_transition(clock, effective_smelt, reserved)
