#!/usr/bin/env python3
"""Read-only M1/M2 CLI: inspect live game state in Chrome and print a dry-run plan."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion.auth import SpaceInvasionAuthError  # noqa: E402
from spaceinvasion.browser import DEFAULT_DEBUG_PORT  # noqa: E402
from spaceinvasion.client import GameBrowserClient  # noqa: E402
from spaceinvasion.state import GameState  # noqa: E402
from spaceinvasion.strategy import PlannedAction, plan_actions  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read live SpaceInvasion Classic state from Chrome and output a dry-run plan."
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=DEFAULT_DEBUG_PORT,
        help=f"Chrome DevTools debug port to attach to (default: {DEFAULT_DEBUG_PORT}).",
    )
    parser.add_argument(
        "--no-buildings",
        action="store_true",
        help="Skip navigating to Buildings tab (reads F_planets + internalHome only).",
    )
    parser.add_argument(
        "--snapshot-out",
        type=Path,
        default=None,
        help="Optional path to save the full JSON snapshot (e.g. state/snapshot.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of formatted text.",
    )
    return parser


def _print_report(state: GameState, actions: list[PlannedAction]) -> None:
    print(f"=== SpaceInvasion Dry-Run Advisor (Uni {state.universe_id} | {state.nickname}) ===")
    flags = []
    if state.speedup_factor:
        flags.append(f"SpeedUp x{state.speedup_factor:g}")
    if state.newbie_protection:
        flags.append("Newbie Protection (<10k pts)")
    if flags:
        print("Status: " + " | ".join(flags))

    unread_non_zero = {k: v for k, v in state.unread_messages.items() if v > 0}
    if unread_non_zero:
        print(f"Unread Messages: {unread_non_zero}")

    for p in state.planets:
        print(
            f"\nPlanet {p.name} [{p.coords}] (id={p.planet_id}, "
            f"fields {p.fields_used}/{p.fields_max}, temp {p.temperature}°C)"
        )
        print(
            f"  Resources: PigIron={p.resources.pig_iron:,} | Metal={p.resources.metal:,} | "
            f"Krypt={p.resources.kryptonite:,} | Spice={p.resources.spice:,}"
        )
        print(
            f"  Energy:    {p.energy.produced} / {p.energy.used} (net {p.energy.net:+d})"
        )
        non_zero_b = [
            f"{p.building_names.get(k, k)} L{lvl}"
            for k, lvl in p.buildings.items()
            if lvl > 0
        ]
        print("  Buildings: " + (", ".join(non_zero_b) if non_zero_b else "none built yet"))

    if state.active_queues:
        print("\nActive Queues:")
        for job in state.active_queues:
            print(f"  - [{job.category}] {job.name}")

    if state.tutorial:
        print(f"\nTutorial {state.tutorial.step} / {state.tutorial.total_steps}:")
        for task in state.tutorial.tasks:
            mark = "DONE" if task.completed else "TODO"
            print(f"  - [{mark}] {task.description}")
        if state.tutorial.reward_text:
            print(f"  Reward: {state.tutorial.reward_text}")

    print("\nDry-Run Action Plan (no changes executed):")
    if not actions:
        print("  (no actions recommended)")
        return
    for idx, act in enumerate(actions, start=1):
        status = "READY" if act.executable_now else "WAIT "
        lvl_str = f" -> L{act.target_level}" if act.target_level else ""
        cost_str = f" | cost: {act.cost.format_short()}" if not act.cost.is_zero() else ""
        miss_str = f" | missing: {act.missing.format_short()}" if not act.missing.is_zero() else ""
        print(
            f"  {idx}. [{act.priority}][{status}] {act.kind}: {act.target_name}{lvl_str} "
            f"@ [{act.planet_coords}]{cost_str}{miss_str}"
        )
        print(f"     Reason: {act.reason}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        with GameBrowserClient.attach(port=args.debug_port) as client:
            state = client.read_full_snapshot(include_buildings=not args.no_buildings)
        actions = plan_actions(state)

        payload = {
            "state": asdict(state),
            "plan": [asdict(a) for a in actions],
        }
        if args.snapshot_out:
            args.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
            args.snapshot_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            _print_report(state, actions)
        return 0
    except SpaceInvasionAuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
