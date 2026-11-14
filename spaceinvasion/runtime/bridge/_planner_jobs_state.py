"""Planner persistence accessors, field-conflict prompts, and record extraction helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import logging
import math
import time

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState
from spaceinvasion.planner import config_optimizer as co_mod
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_float, safe_api_int

_LOGGER = logging.getLogger(__name__)


def extract_record_timestamp(
    rec: Mapping[str, object] | None,
    result: Mapping[str, object] | None = None,
) -> float:
    """Extract the most authoritative UNIX timestamp from a planner run record or result."""
    for src in (rec, result):
        if not isinstance(src, Mapping):
            continue
        for key in ("updated_ts", "created_ts", "generated_ts"):
            val = safe_api_float(src.get(key), 0.0)
            if math.isfinite(val) and val > 0.0:
                return val
        summary_m = src.get("summary")
        if isinstance(summary_m, Mapping):
            ls_ts = safe_api_float(summary_m.get("live_snapshot_ts"), 0.0)
            if math.isfinite(ls_ts) and ls_ts > 0.0:
                return ls_ts
        raw_iso = src.get("generated_at")
        if isinstance(raw_iso, str) and raw_iso.strip():
            try:
                dt = datetime.fromisoformat(raw_iso.strip().replace("Z", "+00:00"))
                ts_v = dt.timestamp()
                if math.isfinite(ts_v) and ts_v > 0.0:
                    return float(ts_v)
            except ValueError:
                _LOGGER.debug("Ignoring unparseable ISO timestamp %r", raw_iso)
    return 0.0


def extract_telemetry_median_spice_eq(tel: object) -> float:
    """Extract median loot spice-equivalent from an attack telemetry mapping."""
    if not isinstance(tel, Mapping):
        return 0.0
    ml = tel.get("median_loot")
    if isinstance(ml, Mapping) and ml.get("spice_eq") is not None:
        return max(0.0, safe_api_float(ml.get("spice_eq"), 0.0))
    if tel.get("spice_eq") is not None:
        return max(0.0, safe_api_float(tel.get("spice_eq"), 0.0))
    return 0.0


def extract_plan_colony_count(active_result: Mapping[str, object]) -> int:
    """Extract the non-placeholder colony count represented by an active AI-Planner result."""
    summary_m = active_result.get("summary")
    if isinstance(summary_m, Mapping):
        for key in ("colonies_founded", "planet_count", "colonies_count", "colonies_target"):
            val = safe_api_int(summary_m.get(key), 0)
            if val > 0:
                return val
    raw_planets = active_result.get("planets")
    if isinstance(raw_planets, Sequence) and not isinstance(raw_planets, (str, bytes)):
        cnt = sum(1 for p in raw_planets if isinstance(p, Mapping) and not bool(p.get("is_placeholder", False)))
        if cnt > 0:
            return cnt
    return 0


def normalize_progress_pct(raw_val: object) -> float:
    """Normalize a 0..1 fraction or 0..100 percentage into `[0.0, 100.0]`."""
    val = safe_api_float(raw_val, 0.0)
    if not math.isfinite(val):
        return 0.0
    if 0.0 < val <= 1.0:
        return round(val * 100.0, 2)
    return round(max(0.0, min(100.0, val)), 2)


def get_planner_state(ctx: BridgeContext) -> GameState | None:
    """Return the latest in-memory or persisted `GameState` for planner runs."""
    if ctx.last_state is not None:
        return ctx.last_state
    if ctx.db is not None:
        try:
            loaded = ctx.db.load_latest_game_state()
            if loaded is not None:
                return loaded
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to load latest persisted game state for planner", exc_info=True)
            return None
    return None


def get_active_config_optimizer(ctx: BridgeContext) -> dict[str, object] | None:
    """Return the active Config Optimizer record from DB or in-memory fallback."""
    if ctx.db is not None:
        rec = ctx.db.get_active_config_optimizer()
        return dict(rec) if isinstance(rec, Mapping) else None
    return dict(ctx.active_config_opt_mem) if isinstance(ctx.active_config_opt_mem, Mapping) else None


def get_config_optimizer_building_goals(ctx: BridgeContext) -> dict[str, object] | None:
    """Return lightweight per-planet Config Optimizer building target levels (`b1..b21`), cached by DB version."""
    tok = ctx.db_total_changes_token()
    ckey = (tok, ctx.db is not None, id(ctx.active_config_opt_mem))
    if tok is not None and ctx.co_goals_cache_key == ckey:
        return ctx.co_goals_cache
    active_co = get_active_config_optimizer(ctx)
    if not isinstance(active_co, Mapping):
        if tok is not None:
            ctx.co_goals_cache_key = ckey
            ctx.co_goals_cache = None
        return None
    raw_res = active_co.get("result")
    res_obj: Mapping[str, object] = raw_res if isinstance(raw_res, Mapping) else active_co
    planets_raw = res_obj.get("planets")
    if not isinstance(planets_raw, Sequence) or isinstance(planets_raw, (str, bytes)):
        if tok is not None:
            ctx.co_goals_cache_key = ckey
            ctx.co_goals_cache = None
        return None
    all_bkeys = tuple(f"b{bid}" for bid in sorted(game_data.BUILDING_BY_ID))
    by_planet_id: dict[str, dict[str, object]] = {}
    by_coords: dict[str, dict[str, object]] = {}
    for p_rec in planets_raw:
        if not isinstance(p_rec, Mapping) or bool(p_rec.get("is_placeholder", False)):
            continue
        pid_i = safe_api_int(p_rec.get("planet_id"), 0)
        coords_s = normalize_coords(str(p_rec.get("coords") or ""))
        spec_s = str(
            p_rec.get("detected_specialization")
            or p_rec.get("locked_specialization")
            or p_rec.get("specialization")
            or ""
        ).strip()
        goals_map: dict[str, int] = {bk: 0 for bk in all_bkeys}
        raw_blds = p_rec.get("buildings")
        if isinstance(raw_blds, Sequence) and not isinstance(raw_blds, (str, bytes)):
            for brow in raw_blds:
                if not isinstance(brow, Mapping):
                    continue
                bkey = str(brow.get("key") or "").strip().lower()
                if bkey in goals_map:
                    goals_map[bkey] = max(0, safe_api_int(brow.get("target_level"), 0))
        entry: dict[str, object] = {
            "planet_id": pid_i,
            "coords": coords_s,
            "specialization": spec_s,
            "goals": goals_map,
        }
        if pid_i > 0:
            by_planet_id[str(pid_i)] = entry
        if coords_s:
            by_coords[coords_s] = entry
    out: dict[str, object] | None = (
        {
            "run_id": active_co.get("run_id"),
            "updated_ts": safe_api_float(active_co.get("updated_ts"), 0.0),
            "by_planet_id": by_planet_id,
            "by_coords": by_coords,
        }
        if (by_planet_id or by_coords)
        else None
    )
    if tok is not None:
        ctx.co_goals_cache_key = ckey
        ctx.co_goals_cache = out
    return out


def list_config_optimizer_runs(ctx: BridgeContext, limit: int = 20) -> list[dict[str, object]]:
    """Return recent Config Optimizer run history."""
    if ctx.db is not None:
        return [dict(r) for r in ctx.db.list_config_optimizer_runs(limit=limit) if isinstance(r, Mapping)]
    return [dict(r) for r in reversed(ctx.config_opt_runs_mem[-limit:])]


def get_reserved_floors(ctx: BridgeContext) -> dict[str, dict[str, int]]:
    """Return per-planet reserved minimum building levels."""
    if ctx.db is not None:
        return ctx.db.get_reserved_building_floors()
    return {pk: dict(bm) for pk, bm in ctx.reserved_floors_mem.items()}


def update_reserved_floor(
    ctx: BridgeContext,
    planet_key: str,
    building_key: str,
    min_level: int,
    *,
    reason: str = "",
) -> dict[str, dict[str, int]]:
    """Persist a reserved building level floor for `planet_key`."""
    if ctx.db is not None:
        return ctx.db.update_reserved_building_floor(planet_key, building_key, min_level, reason=reason)
    p_map = ctx.reserved_floors_mem.setdefault(str(planet_key), {})
    p_map[str(building_key)] = max(max(0, int(min_level)), int(p_map.get(str(building_key), 0)))
    return {pk: dict(bm) for pk, bm in ctx.reserved_floors_mem.items()}


def get_planet_specializations(ctx: BridgeContext) -> dict[str, str]:
    """Return locked planet specialization overrides."""
    if ctx.db is not None:
        return ctx.db.get_config_optimizer_specializations()
    return dict(ctx.planet_specializations_mem)


def update_planet_specializations(
    ctx: BridgeContext,
    specializations: Mapping[str, object],
    *,
    replace: bool = False,
) -> dict[str, str]:
    """Persist updated Config Optimizer planet specializations."""
    if ctx.db is not None:
        updated = ctx.db.update_config_optimizer_specializations(specializations, replace=replace)
        ctx.planet_specializations_mem = dict(updated)
        return dict(updated)
    if replace:
        next_map = co_mod.normalize_planet_specializations_map(specializations)
    else:
        next_map = dict(ctx.planet_specializations_mem)
        for raw_pk, raw_spec in specializations.items():
            pk = str(raw_pk).strip()[:64]
            if not pk:
                continue
            norm_spec = co_mod.normalize_planet_specialization(raw_spec)
            if norm_spec == "auto":
                next_map.pop(pk, None)
            else:
                next_map[pk] = norm_spec
    ctx.planet_specializations_mem = dict(next_map)
    if isinstance(ctx.active_config_opt_mem, dict):
        params_obj = ctx.active_config_opt_mem.get("params")
        if isinstance(params_obj, dict):
            params_obj["planet_specializations"] = dict(next_map)
    return dict(next_map)


def get_active_ai_planner(ctx: BridgeContext) -> dict[str, object] | None:
    """Return the active AI-Planner run record, cached by database change token."""
    if ctx.db is not None:
        tok = ctx.db_total_changes_token()
        ckey = (tok, id(ctx.db)) if tok is not None else None
        if ckey is not None and ctx.active_ai_cache_key == ckey:
            return dict(ctx.active_ai_cache) if isinstance(ctx.active_ai_cache, Mapping) else None
        rec = ctx.db.get_active_ai_planner()
        clean_rec = dict(rec) if isinstance(rec, Mapping) else None
        if ckey is not None:
            ctx.active_ai_cache_key = ckey
            ctx.active_ai_cache = clean_rec
        return dict(clean_rec) if clean_rec is not None else None
    return dict(ctx.active_ai_plan_mem) if isinstance(ctx.active_ai_plan_mem, Mapping) else None


def list_ai_planner_runs(ctx: BridgeContext, limit: int = 20) -> list[dict[str, object]]:
    """Return recent AI-Planner run history."""
    if ctx.db is not None:
        return [dict(r) for r in ctx.db.list_ai_planner_runs(limit=limit) if isinstance(r, Mapping)]
    return [dict(r) for r in reversed(ctx.ai_planner_runs_mem[-limit:])]


def list_field_conflicts(ctx: BridgeContext, status: str | None = "pending") -> list[dict[str, object]]:
    """Return field-conflict prompts overlaid with custom planet display names."""
    if ctx.db is not None:
        raw_items = [dict(r) for r in ctx.db.list_field_conflict_prompts(status=status) if isinstance(r, Mapping)]
    else:
        raw_items = [dict(c) for c in ctx.field_conflicts_mem.values()]
        if status is not None:
            raw_items = [c for c in raw_items if str(c.get("status", "pending")) == status]
    if not ctx.custom_planet_names:
        return raw_items
    out_items: list[dict[str, object]] = []
    for conflict in raw_items:
        cd = dict(conflict)
        cpid = safe_api_int(cd.get("planet_id"), 0)
        if cpid > 0 and ctx.custom_planet_names.get(str(cpid)):
            cd["planet_name"] = ctx.custom_planet_names[str(cpid)]
        out_items.append(cd)
    return out_items


def upsert_field_conflict(ctx: BridgeContext, conflict: Mapping[str, object]) -> dict[str, object]:
    """Insert or update a pending field conflict prompt and raise a deduped notification once."""
    enriched: dict[str, object] = dict(conflict)
    cands = conflict.get("candidates")
    if isinstance(cands, Sequence) and not isinstance(cands, (str, bytes)) and cands:
        first_c = cands[0]
        if isinstance(first_c, Mapping):
            enriched.setdefault("candidate_building_key", str(first_c.get("building_key") or first_c.get("key") or ""))
            enriched.setdefault(
                "candidate_level",
                max(0, safe_api_int(first_c.get("level", first_c.get("current_level", 0)), 0)),
            )
    if ctx.db is not None:
        rec: dict[str, object] = dict(ctx.db.upsert_field_conflict_prompt(enriched))
    else:
        ck = str(enriched.get("conflict_key") or "")
        existing = ctx.field_conflicts_mem.get(ck)
        if existing is not None:
            rec = dict(existing)
        else:
            rec = {
                "conflict_key": ck,
                "planet_id": safe_api_int(enriched.get("planet_id", 0), 0),
                "planet_coords": str(enriched.get("planet_coords") or ""),
                "blocked_building_key": str(enriched.get("blocked_building_key") or ""),
                "blocked_target_level": safe_api_int(enriched.get("blocked_target_level", 0), 0),
                "candidate_building_key": str(enriched.get("candidate_building_key") or ""),
                "candidate_level": safe_api_int(enriched.get("candidate_level", 0), 0),
                "status": "pending",
                "decision": "",
                "created_ts": round(time.time(), 3),
                "resolved_ts": None,
                "detail": dict(enriched),
            }
            ctx.field_conflicts_mem[ck] = rec
    ck_str = str(rec.get("conflict_key") or enriched.get("conflict_key") or "").strip()
    if ck_str and str(rec.get("status", "pending")) == "pending" and ck_str not in ctx.notified_field_conflict_keys:
        ctx.notified_field_conflict_keys.add(ck_str)
        if ctx.db is not None:
            p_coords = str(rec.get("planet_coords") or enriched.get("planet_coords") or "")
            b_name = str(enriched.get("blocked_building_name") or rec.get("blocked_building_key") or "Building")
            b_lvl = safe_api_int(rec.get("blocked_target_level", 0), 0)
            cand_k = str(rec.get("candidate_building_key") or "")
            cand_l = safe_api_int(rec.get("candidate_level", 0), 0)
            ctx.db.create_or_bump_notification(
                title=f"Field conflict on {p_coords or 'colony'}: {b_name} L{b_lvl}",
                message=(
                    f"Planet {p_coords or rec.get('planet_id')} has 0 free building fields for "
                    f"AI-Planner target {b_name} L{b_lvl}. Bot will never auto-demolish — "
                    f"choose Demolish ({cand_k} L{cand_l}) or Keep & Reserve Fields to rerun planner."
                ),
                help_prompt=(
                    "Open Action Graph -> AI-Planner tab and choose Demolish Building or "
                    "Keep & Reserve Fields (Rerun Pipeline)."
                ),
                severity="warning",
                category="field_conflict",
                action_kind="BUILD",
                planet_coords=p_coords,
                target_name=f"{b_name} L{b_lvl}",
                dedupe_key=f"field_conflict:{ck_str}",
                details=rec,
            )
    return rec
