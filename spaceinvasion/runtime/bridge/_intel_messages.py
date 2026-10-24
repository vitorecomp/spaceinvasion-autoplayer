"""Message inbox ingestion, spy-report persistence, and safe client-method helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
import logging
import time

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState
from spaceinvasion.parsing import InboxMessage, SpyReport, parse_inbox_messages_html, parse_spy_messages_html
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    bounded_api_str,
    safe_api_float,
    safe_api_int,
)
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.auth import AntiBotChallengeError
from spaceinvasion.session.client import GameBrowserClient, RateLimitError

_LOGGER = logging.getLogger(__name__)
_NON_SPY_FALLBACK_CATEGORIES: frozenset[str] = frozenset(
    {
        "fleet_return",
        "battle_report",
        "recycle_report",
        "fleet_transport",
        "colonize_report",
        "counter_spy",
    }
)


def _has_defined_callable(obj: object, attr_name: str) -> bool:
    for cls in type(obj).__mro__:
        if attr_name in cls.__dict__:
            val = cls.__dict__[attr_name]
            return callable(val) or isinstance(val, (classmethod, staticmethod))
    try:
        obj_dict = vars(obj)
    except TypeError:
        return False
    return attr_name in obj_dict and callable(obj_dict[attr_name])


def is_real_client_method(client: GameBrowserClient | None, attr_name: str) -> bool:
    """Return True when `client` directly implements `attr_name` (not an auto-created Mock child)."""
    if client is None:
        return False
    try:
        inst_dict = vars(client)
    except TypeError:
        inst_dict = {}
    if "unittest.mock" in type(client).__module__:
        if attr_name in inst_dict:
            return callable(inst_dict[attr_name])
        wraps = inst_dict.get("_mock_wraps")
        if wraps is not None:
            return _has_defined_callable(wraps, attr_name)
        return False
    return _has_defined_callable(client, attr_name)


def extract_client_current_url(client: GameBrowserClient | None) -> str:
    """Safely extract `client.driver.current_url` as a string without using `getattr`."""
    if client is None:
        return ""
    try:
        drv = client.driver
    except AttributeError:
        return ""
    if drv is None:
        return ""
    try:
        raw_url = drv.current_url
    except BRIDGE_RUNTIME_ERRORS:
        return ""
    return str(raw_url or "")


def own_planet_coords_for_ctx(ctx: BridgeContext, state: GameState | None = None) -> set[str]:
    """Collect normalized coordinates of all owned planets from `state` and cached `ctx.last_state`."""
    coords: set[str] = set()
    for src in (state, ctx.last_state):
        if src is None:
            continue
        for planet in src.planets:
            nc = normalize_coords(planet.coords or "")
            if nc:
                coords.add(nc)
    return coords


def persist_spy_reports_for_ctx(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    reports: Sequence[SpyReport],
    *,
    exclude_coords: set[str] | None = None,
) -> int:
    """Persist parsed spy reports to DB (or in-memory raid targets fallback)."""
    if not reports:
        return 0
    excl = exclude_coords if exclude_coords is not None else own_planet_coords_for_ctx(ctx)
    filtered = [
        rep
        for rep in reports
        if normalize_coords(rep.target_coords) and normalize_coords(rep.target_coords) not in excl
    ]
    if not filtered:
        return 0
    now_ts = round(time.time(), 3)
    if ctx.db is not None:
        for rep in filtered:
            ctx.db.record_spy_report(rep, ts=now_ts)
        if any(rep.researches for rep in filtered):
            ctx.db.refresh_universe_research_max(
                own_researches=(ctx.last_state.researches if ctx.last_state is not None else None),
                own_nick=(ctx.last_state.nickname if ctx.last_state is not None else ""),
                source="spy_report",
                ts=now_ts,
            )
        state_cache.refresh_raid_targets_locked(limit=100)
        return len(filtered)
    by_coords: dict[str, dict[str, object]] = {
        normalize_coords(str(r.get("coords") or r.get("target_coords") or "")): dict(r)
        for r in ctx.raid_targets
        if (r.get("coords") or r.get("target_coords"))
    }
    for rep in filtered:
        norm_tc = normalize_coords(rep.target_coords)
        prev = by_coords.get(norm_tc, {})
        is_spacia = bool(rep.is_spacia or prev.get("is_spacia", False))
        status_color = str(prev.get("status_color") or ("yellow" if is_spacia else "light_red"))
        by_coords[norm_tc] = {
            "coords": norm_tc,
            "owner": rep.owner or str(prev.get("owner") or ""),
            "planet_name": rep.planet_name or str(prev.get("planet_name") or ""),
            "status_color": status_color,
            "is_inactive": bool(prev.get("is_inactive", False) or is_spacia or status_color == "yellow"),
            "is_spacia": is_spacia,
            "has_debris": bool(prev.get("has_debris", False)),
            "points": prev.get("points"),
            "resources": asdict(rep.resources),
            "fleet": dict(rep.fleet),
            "defense": dict(rep.defense),
            "dug_in": bool(rep.dug_in),
            "last_scanned_ts": max(safe_api_float(prev.get("last_scanned_ts"), 0.0), now_ts),
            "last_spied_ts": now_ts,
            "has_spy_intel": True,
            "blocked_until_ts": safe_api_float(prev.get("blocked_until_ts"), 0.0),
            "block_reason": bounded_api_str(prev.get("block_reason") or "", default=""),
            "bash_hits": safe_api_int(prev.get("bash_hits"), 0),
            "last_blocked_ts": safe_api_float(prev.get("last_blocked_ts"), 0.0),
        }
    ctx.raid_targets = state_cache.enrich_raid_targets(list(by_coords.values())[:100])
    return len(filtered)


def _expand_messages_html(client: GameBrowserClient | None, html: str) -> str:
    if client is None or not is_real_client_method(client, "expand_collapsed_messages_on_page"):
        return html
    try:
        expanded = client.expand_collapsed_messages_on_page()
        if isinstance(expanded, str) and expanded.strip():
            return expanded
    except (RateLimitError, AntiBotChallengeError):
        raise
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to expand collapsed messages on page", exc_info=True)
    return html


def _extract_page_spy_reports(
    html: str,
    messages: Sequence[InboxMessage],
    *,
    folder_type: str,
    excl: set[str],
) -> list[SpyReport]:
    non_spy_ids = {m.server_msg_id for m in messages if m.server_msg_id and m.category != "spy_report"}
    f_low = (folder_type or "").strip().lower()
    page_spy: list[SpyReport] = (
        [
            rep
            for rep in parse_spy_messages_html(html, exclude_coords=excl)
            if not (rep.server_msg_id and rep.server_msg_id in non_spy_ids)
        ]
        if f_low in ("", "spio", "inbox")
        else []
    )
    seen_keys = {(normalize_coords(r.target_coords) or r.target_coords, r.server_msg_id) for r in page_spy}
    for msg in messages:
        if msg.spy_report is None:
            continue
        rk = (
            normalize_coords(msg.spy_report.target_coords) or msg.spy_report.target_coords,
            msg.spy_report.server_msg_id,
        )
        if rk not in seen_keys:
            seen_keys.add(rk)
            page_spy.append(msg.spy_report)
    return page_spy


def _select_reports_to_persist(
    ctx: BridgeContext,
    messages: Sequence[InboxMessage],
    newly_inserted: Sequence[InboxMessage],
    page_spy_reports: list[SpyReport],
    *,
    always_persist_spy: bool,
) -> list[SpyReport]:
    if ctx.db is None or always_persist_spy or not messages:
        return page_spy_reports
    reports_out: list[SpyReport] = []
    seen_new: set[tuple[str, str]] = set()
    for msg in newly_inserted:
        if msg.spy_report is None:
            continue
        rk = (
            normalize_coords(msg.spy_report.target_coords) or msg.spy_report.target_coords,
            msg.spy_report.server_msg_id,
        )
        if rk not in seen_new:
            seen_new.add(rk)
            reports_out.append(msg.spy_report)
    if (
        not any(m.spy_report is not None for m in messages)
        and page_spy_reports
        and not any(m.category in _NON_SPY_FALLBACK_CATEGORIES for m in messages)
    ):
        return page_spy_reports
    return reports_out


def _process_newly_inserted_side_effects(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    newly_inserted: Sequence[InboxMessage],
    *,
    excl: set[str],
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None,
) -> None:
    debris_changed = False
    for msg in newly_inserted:
        if msg.category == "fleet_transport" and on_transport_delivery_fn is not None:
            try:
                on_transport_delivery_fn(msg)
            except BRIDGE_RUNTIME_ERRORS:
                _LOGGER.warning("Transport delivery hook failed", exc_info=True)
        if ctx.db is None:
            continue
        mc = normalize_coords(msg.coords or "")
        if not mc:
            continue
        parsed = msg.parsed_data if isinstance(msg.parsed_data, Mapping) else {}
        if msg.category == "battle_report" and bool(parsed.get("has_debris")):
            if mc not in excl:
                ctx.db.set_target_debris(mc, True)
                debris_changed = True
        elif msg.category == "recycle_report":
            ctx.db.set_target_debris(mc, False)
            ctx.galaxy_debris_amounts.pop(mc, None)
            debris_changed = True
    if debris_changed:
        state_cache.refresh_raid_targets_locked(limit=100)


def _delete_persisted_messages_on_page(
    ctx: BridgeContext,
    client: GameBrowserClient | None,
    html: str,
    messages: Sequence[InboxMessage],
    persisted_uids: Sequence[str],
    *,
    priority: str,
    delete_after_persist: bool,
) -> tuple[bool, str]:
    complete_msgs = [m for m in messages if bool(m.body_complete)]
    all_complete = len(complete_msgs) == len(messages)
    can_delete = (
        delete_after_persist
        and bool(complete_msgs)
        and (
            ctx.db is None
            or (all_complete and len(persisted_uids) == len(messages))
            or (not all_complete and len(persisted_uids) >= len(complete_msgs))
        )
        and client is not None
        and is_real_client_method(client, "delete_messages_on_current_page")
    )
    if not can_delete or client is None:
        return False, html
    server_ids = [m.server_msg_id for m in complete_msgs if m.server_msg_id]
    if not all_complete and not server_ids:
        return False, html
    deleted = False
    post_html = html
    try:
        raw_res = call_with_optional_priority(
            client.delete_messages_on_current_page,
            server_ids,
            all_on_page_persisted=all_complete,
            priority=priority,
        )
        if isinstance(raw_res, tuple) and len(raw_res) == 2:
            deleted = bool(raw_res[0])
            if isinstance(raw_res[1], str) and raw_res[1]:
                post_html = raw_res[1]
    except (RateLimitError, AntiBotChallengeError):
        raise
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to delete messages on page", exc_info=True)
        deleted = False
    if deleted and ctx.db is not None and persisted_uids:
        if all_complete:
            ctx.db.mark_game_messages_deleted(persisted_uids)
        else:
            complete_uids = [m.msg_uid for m in complete_msgs if m.msg_uid]
            if complete_uids:
                ctx.db.mark_game_messages_deleted(complete_uids)
    return deleted, post_html


def ingest_page_messages_for_ctx(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient | None,
    html: str,
    *,
    folder_type: str = "",
    own_coords: set[str] | None = None,
    priority: str = "normal",
    always_persist_spy: bool = False,
    delete_after_persist: bool = True,
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None = None,
) -> tuple[list[SpyReport], int, bool, str]:
    """Parse, persist, trigger side-effects, and optionally delete inbox messages on one page."""
    if not isinstance(html, str) or not html.strip():
        return ([], 0, False, "")
    excl = own_coords if own_coords is not None else own_planet_coords_for_ctx(ctx)
    eff_html = _expand_messages_html(client, html)
    messages = parse_inbox_messages_html(eff_html, folder_type=folder_type, exclude_coords=excl)
    page_spy_reports = _extract_page_spy_reports(eff_html, messages, folder_type=folder_type, excl=excl)

    persisted_uids: list[str] = []
    newly_inserted: list[InboxMessage] = list(messages)
    if ctx.db is not None and messages:
        persisted_uids, newly_inserted = ctx.db.record_game_messages(messages)

    reports_to_persist = _select_reports_to_persist(
        ctx,
        messages,
        newly_inserted,
        page_spy_reports,
        always_persist_spy=always_persist_spy,
    )
    if reports_to_persist:
        persist_spy_reports_for_ctx(ctx, state_cache, reports_to_persist, exclude_coords=excl)

    _process_newly_inserted_side_effects(
        ctx,
        state_cache,
        newly_inserted,
        excl=excl,
        on_transport_delivery_fn=on_transport_delivery_fn,
    )
    deleted, post_html = _delete_persisted_messages_on_page(
        ctx,
        client,
        eff_html,
        messages,
        persisted_uids,
        priority=priority,
        delete_after_persist=delete_after_persist,
    )
    ingested_count = max(len(reports_to_persist), len(newly_inserted))
    return (page_spy_reports, ingested_count, deleted, post_html)
