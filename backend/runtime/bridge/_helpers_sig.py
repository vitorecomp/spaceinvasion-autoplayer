"""Signature, UI asset digest, fleet ranking, and callable-introspection helpers for the bridge."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import inspect
import math
from pathlib import Path
import threading

from backend.core.model import GameState, PlannedAction
from backend.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    redact_api_payload,
    resolve_ui_dir,
    scrub_api_str,
)

_ASSET_DIGEST_LOCK = threading.Lock()
_ASSET_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}
_MAX_ASSET_DIGEST_CACHE = 256


def fleet_subrank(action: PlannedAction) -> int:
    """Return intra-priority sort rank for fleet and tutorial actions."""
    if action.priority == "P1":
        if action.kind == "ABANDON_COLONY":
            return 0
        if action.kind == "SPY":
            return 1 if "(free slot)" in str(action.target_name or "").lower() else 2
        if action.kind == "TRANSPORT":
            return 3
        if action.kind == "COLONIZE":
            return 4
        return 5
    if action.priority == "P2":
        if action.kind == "CLAIM_TUTORIAL":
            return 0
        if action.kind == "SPY":
            return 1
        if action.kind == "TRANSPORT":
            return 2
        if action.kind == "COLONIZE":
            return 3
        if action.kind == "STATION":
            return 4
        return 5
    return 0


def compute_ui_assets_state(ui_dir: Path | None = None) -> dict[str, str]:
    """Return content-SHA1 digest tokens for hot-reloading UI CSS and code assets."""
    eff_dir = resolve_ui_dir(ui_dir)

    def _file_sig(name: str) -> str:
        path = eff_dir / name
        try:
            st = path.stat()
            cache_key = (str(path.resolve()), int(st.st_mtime_ns), int(st.st_size))
            with _ASSET_DIGEST_LOCK:
                cached = _ASSET_DIGEST_CACHE.get(cache_key)
            if cached is not None:
                return cached
            digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
            with _ASSET_DIGEST_LOCK:
                if len(_ASSET_DIGEST_CACHE) >= _MAX_ASSET_DIGEST_CACHE:
                    _ASSET_DIGEST_CACHE.clear()
                _ASSET_DIGEST_CACHE[cache_key] = digest
            return digest
        except OSError:
            return "missing"

    css_version = _file_sig("styles.css")
    code_raw = f"{_file_sig('index.html')}|{_file_sig('app.js')}".encode()
    code_version = hashlib.sha1(code_raw).hexdigest()[:12]
    ui_version = f"{code_version}:{css_version}"
    return {
        "ui_version": ui_version,
        "code_version": code_version,
        "css_version": css_version,
    }


def redact_action_graph_strings(graph: dict[str, object]) -> dict[str, object]:
    """Scrub secret tokens in-place on an action-graph response dict."""
    lanes = graph.get("lanes")
    if isinstance(lanes, Sequence) and not isinstance(lanes, (str, bytes)):
        for lane in lanes:
            if isinstance(lane, dict):
                for key in ("label", "coords"):
                    val = lane.get(key)
                    if isinstance(val, str) and val:
                        lane[key] = scrub_api_str(val)
    nodes = graph.get("nodes")
    if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
        for node in nodes:
            if isinstance(node, dict):
                for key in (
                    "planet_coords",
                    "planet_name",
                    "target_name",
                    "reason",
                    "wait_eta",
                    "duration_text",
                    "total_eta",
                    "eta_bottleneck",
                ):
                    val = node.get(key)
                    if isinstance(val, str) and val:
                        node[key] = scrub_api_str(val)
    edges = graph.get("edges")
    if isinstance(edges, Sequence) and not isinstance(edges, (str, bytes)):
        for edge in edges:
            if isinstance(edge, dict):
                lbl = edge.get("label")
                if isinstance(lbl, str) and lbl:
                    edge["label"] = scrub_api_str(lbl)
    summary = graph.get("summary")
    if isinstance(summary, dict):
        for key in ("critical_path_eta", "primary_bottleneck"):
            val = summary.get(key)
            if isinstance(val, str) and val:
                summary[key] = scrub_api_str(val)
    if graph.get("ga_summary") is not None:
        graph["ga_summary"] = redact_api_payload(graph["ga_summary"])
    return graph


def safe_sig_eq(left: object, right: object) -> bool:
    """Compare two cache signatures safely without propagating comparison errors."""
    if left is right:
        return True
    if left is None or right is None:
        return False
    try:
        return bool(left == right)
    except BRIDGE_RUNTIME_ERRORS:
        return False


def summary_without_elapsed(summary: object) -> object:
    """Return a copy of a summary mapping omitting volatile `elapsed_ms`."""
    if isinstance(summary, Mapping):
        return {k: v for k, v in summary.items() if k != "elapsed_ms"}
    return summary


def freeze_sig_tree(val: object, _depth: int = 0, _seen: set[int] | None = None) -> object:
    """Convert a nested object tree into a hashable/comparable deterministic cache key."""
    tv = type(val)
    if val is None or tv is int or tv is str or tv is bytes:
        return val
    if tv is bool:
        return ("__bool__", val)
    if isinstance(val, float):
        fval = float(val)
        if math.isnan(fval):
            return ("__nan__",)
        if math.isinf(fval):
            return ("__inf__", 1 if fval > 0 else -1)
        return fval
    if _depth >= 32:
        return repr(val)
    if isinstance(val, (Mapping, list, tuple, set, frozenset)):
        seen = _seen if _seen is not None else set()
        vid = id(val)
        if vid in seen:
            return ("__cycle__",)
        seen.add(vid)
        try:
            if isinstance(val, Mapping):
                items = [
                    (k if type(k) is str else f"{type(k).__name__}:{k}", freeze_sig_tree(v, _depth + 1, seen))
                    for k, v in val.items()
                ]
                return tuple(sorted(items, key=lambda kv: kv[0]))
            if isinstance(val, (list, tuple)):
                return tuple(freeze_sig_tree(x, _depth + 1, seen) for x in val)
            return ("__set__", tuple(freeze_sig_tree(x, _depth + 1, seen) for x in sorted(val, key=repr)))
        finally:
            seen.discard(vid)
    return val


def dashboard_state_sig(
    last_state: GameState | None,
    dash_state: GameState | None,
    rank_samples: Sequence[object],
) -> object:
    """Build a deterministic signature key for cached dashboard rendering."""
    if rank_samples:
        first_map = next((s for s in rank_samples if isinstance(s, Mapping)), None)
        rank_sig: object = (
            len(rank_samples),
            freeze_sig_tree(rank_samples[0]),
            freeze_sig_tree(first_map) if first_map is not rank_samples[0] else None,
            freeze_sig_tree(rank_samples[-20:]),
        )
    else:
        rank_sig = (0,)
    if dash_state is None:
        return (None, rank_sig)
    return (
        id(last_state),
        dash_state.universe_id,
        dash_state.user_id,
        dash_state.nickname,
        freeze_sig_tree(dash_state.timestamp),
        dash_state.active_planet_id,
        freeze_sig_tree(dash_state.researches),
        freeze_sig_tree(dash_state.research_names),
        freeze_sig_tree(dash_state.specs),
        freeze_sig_tree(dash_state.unread_messages),
        dash_state.tutorial,
        dash_state.active_queues,
        dash_state.fleet_alerts,
        dash_state.research_offers,
        freeze_sig_tree(dash_state.speedup_factor),
        dash_state.newbie_protection,
        freeze_sig_tree(dash_state.account_points),
        dash_state.account_rank,
        freeze_sig_tree(dash_state.raid_targets or ()),
        freeze_sig_tree(dash_state.raid_stats),
        tuple(
            (
                p.planet_id,
                p.name,
                p.coords,
                p.planet_type,
                p.temperature,
                p.fields_used,
                p.fields_max,
                p.resources,
                p.storage,
                p.energy,
                freeze_sig_tree(p.buildings),
                freeze_sig_tree(p.building_names),
                freeze_sig_tree(p.ships),
                freeze_sig_tree(p.defense),
                p.building_offers,
                p.ship_offers,
                p.defense_offers,
                freeze_sig_tree(p.economy_rates),
            )
            for p in (dash_state.planets or ())
        ),
        rank_sig,
    )


def supports_kwarg(fn: Callable[..., object], kwarg_name: str) -> bool:
    """Return True if `fn` accepts `kwarg_name` (either explicitly or via `**kwargs`)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return kwarg_name in sig.parameters or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()
    )


def call_with_optional_priority(
    fn: Callable[..., object],
    *args: object,
    priority: str = "interactive",
    **kwargs: object,
) -> object:
    """Invoke `fn`, forwarding `priority=` only when its signature accepts it."""
    call_kwargs = dict(kwargs)
    if supports_kwarg(fn, "priority"):
        call_kwargs["priority"] = priority
    return fn(*args, **call_kwargs)
