#!/usr/bin/env python3
"""Read-only M1/M2 CLI: inspect live game state in Chrome and print a dry-run plan."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = _REPO_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion import game_data  # noqa: E402
from spaceinvasion.auth import SpaceInvasionAuthError  # noqa: E402
from spaceinvasion.browser import DEFAULT_DEBUG_PORT  # noqa: E402
from spaceinvasion.client import GameBrowserClient  # noqa: E402
from spaceinvasion.logging_utils import redact_sensitive  # noqa: E402
from spaceinvasion.state import GameState  # noqa: E402
from spaceinvasion.strategy import PlannedAction, plan_actions  # noqa: E402


def _safe_cli_str(
    val: object,
    default: str = "",
    *,
    max_len: int = 1024,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return default
        if val is None:
            return default
        if isinstance(val, os.PathLike):
            return default
    if isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, BaseException):
        str_fn = getattr(type(val), "__str__", BaseException.__str__)
        used_custom_str = False
        txt = ""
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                txt = str(val)[:16_384]
                used_custom_str = True
            except Exception:
                txt = ""
        if not used_custom_str:
            args = getattr(val, "args", ())
            if not isinstance(args, (tuple, list)) or not args:
                txt = "" if str_fn is BaseException.__str__ else type(val).__name__
            else:
                seen = _seen if _seen is not None else set()
                budget = _budget if _budget is not None else [256]
                vid = id(val)
                if vid in seen or _depth >= 16 or budget[0] <= 0:
                    txt = f"<{type(val).__name__}>"
                else:
                    budget[0] -= 1
                    seen.add(vid)
                    try:
                        if len(args) == 1:
                            a0 = args[0]
                            if isinstance(a0, str):
                                txt = (repr(a0) if isinstance(val, KeyError) else a0)[:16_384]
                            else:
                                txt = _safe_cli_str(
                                    a0,
                                    f"<{type(a0).__name__}>",
                                    max_len=4096,
                                    _depth=_depth + 1,
                                    _seen=seen,
                                    _budget=budget,
                                )
                        else:
                            parts = []
                            for a in args[:8]:
                                if budget[0] <= 0:
                                    parts.append("...")
                                    break
                                if isinstance(a, str):
                                    parts.append(repr(a))
                                else:
                                    parts.append(
                                        _safe_cli_str(
                                            a,
                                            f"<{type(a).__name__}>",
                                            max_len=4096,
                                            _depth=_depth + 1,
                                            _seen=seen,
                                            _budget=budget,
                                        )
                                    )
                            if len(args) > 8:
                                parts.append(f"...+{len(args) - 8}")
                            txt = ("(" + ", ".join(parts) + ")")[:4096]
                    finally:
                        seen.discard(vid)
    elif isinstance(val, int) and not isinstance(val, bool):
        txt = game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    else:
        try:
            txt = game_data.safe_key(val)
        except Exception:
            return default
    txt = str(redact_sensitive(txt))
    return txt[:max_len] if len(txt) > max_len else txt


def _coerce_cli_json_key(
    key: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> object:
    if key is None or isinstance(key, (bool, str)):
        return key
    if isinstance(key, int):
        if -game_data.MAX_JSON_SAFE_INT <= key <= game_data.MAX_JSON_SAFE_INT:
            return key
        return _safe_cli_str(key, default="key", max_len=256)
    if isinstance(key, float):
        return key if math.isfinite(key) else "null"
    if isinstance(key, (bytes, bytearray)):
        return bytes(key).decode("utf-8", errors="replace")
    if isinstance(key, BaseException):
        return _safe_cli_str(key, default="<unprintable>", max_len=512)
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [256]
    if isinstance(key, os.PathLike):
        vid = id(key)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return "<unprintable>"
        budget[0] -= 1
        seen.add(vid)
        try:
            try:
                fsp = os.fspath(key)
            except Exception:
                return "<unprintable>"
            return _coerce_cli_json_key(fsp, _depth=_depth + 1, _seen=seen, _budget=budget)
        finally:
            seen.discard(vid)
    if isinstance(key, (tuple, frozenset, list, set, Mapping)):
        vid = id(key)
        if vid in seen:
            if isinstance(key, tuple):
                return "(...)"
            if isinstance(key, list):
                return "[...]"
            if isinstance(key, frozenset):
                return "frozenset({...})"
            return "{...}"
        if _depth >= 16 or budget[0] <= 0:
            return "..."
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(key, tuple):
                parts: list[str] = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(str(_coerce_cli_json_key(el, _depth=_depth + 1, _seen=seen, _budget=budget)))
                inner = ", ".join(parts)
                return (f"({inner},)" if len(key) == 1 else f"({inner})")[:4096]
            if isinstance(key, frozenset):
                parts = []
                for idx, el in enumerate(key):
                    if idx >= 64 or budget[0] <= 0:
                        parts.append("...")
                        break
                    parts.append(str(_coerce_cli_json_key(el, _depth=_depth + 1, _seen=seen, _budget=budget)))
                inner = ", ".join(sorted(parts))
                return f"frozenset({{{inner}}})"[:4096]
            return game_data.safe_key(key)
        finally:
            seen.discard(vid)
    return _safe_cli_str(key, default="<unprintable>", max_len=512)


def _coerce_cli_json_scalars(
    obj: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> object:
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        if -game_data.MAX_JSON_SAFE_INT <= obj <= game_data.MAX_JSON_SAFE_INT:
            return obj
        return _safe_cli_str(obj, default="0", max_len=256)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).decode("utf-8", errors="replace")
    if isinstance(obj, BaseException):
        return _safe_cli_str(obj, default="<unprintable>", max_len=1024)
    if isinstance(obj, (Mapping, list, tuple, set, frozenset, os.PathLike)):
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [32_768]
        vid = id(obj)
        if vid in seen:
            return "<unprintable>" if isinstance(obj, os.PathLike) else "[Cycle]"
        if _depth >= 64 or budget[0] <= 0:
            return "<unprintable>" if isinstance(obj, os.PathLike) else "[Truncated]"
        budget[0] -= 1
        seen.add(vid)
        try:
            if isinstance(obj, os.PathLike):
                try:
                    raw = os.fspath(obj)
                except Exception:
                    return "<unprintable>"
                if isinstance(raw, (bytes, bytearray)):
                    return bytes(raw).decode("utf-8", errors="replace")
                return raw if isinstance(raw, str) else "<unprintable>"
            if isinstance(obj, Mapping):
                out: dict[str, object] = {}
                for k, v in obj.items():
                    if budget[0] <= 0:
                        out["[Truncated]"] = "[Truncated]"
                        break
                    out[str(_coerce_cli_json_key(k))] = _coerce_cli_json_scalars(
                        v, _depth=_depth + 1, _seen=seen, _budget=budget
                    )
                return out
            out_l: list[object] = []
            for v in obj:
                if budget[0] <= 0:
                    out_l.append("[Truncated]")
                    break
                out_l.append(
                    _coerce_cli_json_scalars(v, _depth=_depth + 1, _seen=seen, _budget=budget)
                )
            return out_l
        finally:
            seen.discard(vid)
    return _safe_cli_str(obj, default="<unprintable>", max_len=1024)


def _safe_cli_json(payload: object) -> str:
    pre_redacted = redact_sensitive(payload)
    coerced = _coerce_cli_json_scalars(pre_redacted)
    sanitized = game_data.sanitize_json_tree(
        coerced,
        max_int=game_data.MAX_JSON_SAFE_INT,
        nonfinite_float=None,
    )
    clean = redact_sensitive(sanitized)
    return json.dumps(clean, indent=2, allow_nan=False)


def _safe_cli_float(val: object) -> float | None:
    if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    try:
        f = float(val)
    except Exception:
        return None
    return f if math.isfinite(f) else None


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
    uid_txt = game_data.fmt_compact_int(state.universe_id)
    nick_txt = redact_sensitive(_safe_cli_str(state.nickname))
    print(f"=== SpaceInvasion Dry-Run Advisor (Uni {uid_txt} | {nick_txt}) ===")
    flags = []
    sf_f = _safe_cli_float(state.speedup_factor)
    if sf_f is not None and sf_f:
        flags.append(f"SpeedUp x{sf_f:g}")
    if state.newbie_protection:
        flags.append("Newbie Protection (<10k pts)")
    if flags:
        print("Status: " + " | ".join(flags))

    unread_non_zero = {
        redact_sensitive(game_data.safe_key(k)): game_data.fmt_compact_int(v)
        for k, v in (state.unread_messages or {}).items()
        if isinstance(v, int) and not isinstance(v, bool) and v > 0
    }
    if unread_non_zero:
        print(f"Unread Messages: {unread_non_zero}")

    for p in state.planets:
        pname = redact_sensitive(_safe_cli_str(p.name))
        pcoords = redact_sensitive(_safe_cli_str(p.coords))
        pid_txt = game_data.fmt_compact_int(p.planet_id)
        fu_txt = game_data.fmt_compact_int(p.fields_used)
        fm_txt = game_data.fmt_compact_int(p.fields_max)
        temp_txt = game_data.fmt_compact_int(p.temperature)
        print(
            f"\nPlanet {pname} [{pcoords}] (id={pid_txt}, "
            f"fields {fu_txt}/{fm_txt}, temp {temp_txt}°C)"
        )
        pig_txt = game_data.fmt_compact_int(p.resources.pig_iron, grouped=True)
        met_txt = game_data.fmt_compact_int(p.resources.metal, grouped=True)
        kry_txt = game_data.fmt_compact_int(p.resources.kryptonite, grouped=True)
        spi_txt = game_data.fmt_compact_int(p.resources.spice, grouped=True)
        print(
            f"  Resources: PigIron={pig_txt} | Metal={met_txt} | "
            f"Krypt={kry_txt} | Spice={spi_txt}"
        )
        ep_txt = game_data.fmt_compact_int(p.energy.produced)
        eu_txt = game_data.fmt_compact_int(p.energy.used)
        net_val = game_data.safe_int(p.energy.net, 0)
        net_prefix = "+" if net_val >= 0 else ""
        net_txt = f"{net_prefix}{game_data.fmt_compact_int(net_val)}"
        print(
            f"  Energy:    {ep_txt} / {eu_txt} (net {net_txt})"
        )
        non_zero_b = [
            f"{redact_sensitive(game_data.safe_key(p.building_names.get(k, k)))} L{game_data.fmt_compact_int(lvl)}"
            for k, lvl in p.buildings.items()
            if isinstance(lvl, int) and not isinstance(lvl, bool) and lvl > 0
        ]
        print("  Buildings: " + (", ".join(non_zero_b) if non_zero_b else "none built yet"))

    if state.active_queues:
        print("\nActive Queues:")
        for job in state.active_queues:
            print(f"  - [{redact_sensitive(_safe_cli_str(job.category))}] {redact_sensitive(_safe_cli_str(job.name))}")

    if state.tutorial:
        step_txt = game_data.fmt_compact_int(state.tutorial.step)
        tot_txt = game_data.fmt_compact_int(state.tutorial.total_steps)
        print(f"\nTutorial {step_txt} / {tot_txt}:")
        for task in state.tutorial.tasks:
            mark = "DONE" if task.completed else "TODO"
            print(f"  - [{mark}] {redact_sensitive(_safe_cli_str(task.description))}")
        if state.tutorial.reward_text:
            print(f"  Reward: {redact_sensitive(_safe_cli_str(state.tutorial.reward_text))}")

    print("\nDry-Run Action Plan (no changes executed):")
    if not actions:
        print("  (no actions recommended)")
        return
    for idx, act in enumerate(actions, start=1):
        status = "READY" if act.executable_now else "WAIT "
        lvl_str = f" -> L{game_data.fmt_compact_int(act.target_level)}" if act.target_level else ""
        cost_str = f" | cost: {act.cost.format_short()}" if not act.cost.is_zero() else ""
        miss_str = f" | missing: {act.missing.format_short()}" if not act.missing.is_zero() else ""
        tname = redact_sensitive(_safe_cli_str(act.target_name))
        pcoords = redact_sensitive(_safe_cli_str(act.planet_coords))
        reason_txt = redact_sensitive(_safe_cli_str(act.reason))
        prio_txt = redact_sensitive(_safe_cli_str(act.priority))
        kind_txt = redact_sensitive(_safe_cli_str(act.kind))
        print(
            f"  {idx}. [{prio_txt}][{status}] {kind_txt}: {tname}{lvl_str} "
            f"@ [{pcoords}]{cost_str}{miss_str}"
        )
        print(f"     Reason: {reason_txt}")


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
            args.snapshot_out.write_text(_safe_cli_json(payload), encoding="utf-8")

        if args.json:
            print(_safe_cli_json(payload))
        else:
            _print_report(state, actions)
        return 0
    except SpaceInvasionAuthError as exc:
        err_txt = redact_sensitive(_safe_cli_str(exc))
        if args.json:
            print(_safe_cli_json({"ok": False, "error": err_txt}))
        else:
            print(f"Error: {err_txt}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        err_txt = redact_sensitive(_safe_cli_str(exc))
        if args.json:
            print(_safe_cli_json({"ok": False, "error": err_txt}))
        else:
            print(f"Unexpected error: {err_txt}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
