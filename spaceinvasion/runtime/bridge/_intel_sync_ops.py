"""Interactive multi-folder message archival (`sync_messages`) helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from spaceinvasion.core.model import GameState
from spaceinvasion.parsing import InboxMessage, SpyReport, extract_message_folder_links
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import redact_api_payload, sanitize_json_dict
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority
from spaceinvasion.runtime.bridge._intel_messages import (
    extract_client_current_url,
    ingest_page_messages_for_ctx,
    own_planet_coords_for_ctx,
)
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.client import GameBrowserClient

_EXTRA_MESSAGE_FOLDERS: tuple[str, ...] = ("battle", "fleet", "normal", "alliance", "admin")


def _ingest_spio_and_inbox_pages(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient,
    *,
    own_coords: set[str],
    priority: str,
    delete_processed: bool,
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None,
) -> tuple[list[SpyReport], int, bool, dict[str, dict[str, int]]]:
    raw_html = call_with_optional_priority(client.open_messages, "spio", priority=priority)
    html = raw_html if isinstance(raw_html, str) else ""
    reports, ingested_cnt, del_ok, spio_post = ingest_page_messages_for_ctx(
        ctx,
        state_cache,
        client,
        html,
        folder_type="spio",
        own_coords=own_coords,
        priority=priority,
        always_persist_spy=True,
        delete_after_persist=delete_processed,
        on_transport_delivery_fn=on_transport_delivery_fn,
    )
    folder_links = extract_message_folder_links(spio_post or html)
    folder_counts: dict[str, dict[str, int]] = {
        ft: {"unread": int(uc), "total": int(tot)} for ft, _hr, uc, tot in folder_links if ft
    }
    if del_ok and folder_counts.get("spio", {}).get("total", 0) > 0 and ctx.rate_limit_remaining() <= 0.0:
        raw2 = call_with_optional_priority(client.open_messages, "spio", priority=priority)
        html2 = raw2 if isinstance(raw2, str) else ""
        rep2, ing2, del2, post2 = ingest_page_messages_for_ctx(
            ctx,
            state_cache,
            client,
            html2,
            folder_type="spio",
            own_coords=own_coords,
            priority=priority,
            always_persist_spy=True,
            delete_after_persist=delete_processed,
            on_transport_delivery_fn=on_transport_delivery_fn,
        )
        reports.extend(rep2)
        ingested_cnt += ing2
        del_ok = del_ok or del2
        for ft2, _h2, u2, t2 in extract_message_folder_links(post2 or html2):
            if ft2:
                folder_counts[ft2] = {"unread": int(u2), "total": int(t2)}
    if not folder_links and not reports and ingested_cnt == 0:
        raw_inb = call_with_optional_priority(client.open_messages, "", priority=priority)
        inb_html = raw_inb if isinstance(raw_inb, str) else ""
        inb_rep, inb_cnt, inb_del, inb_post = ingest_page_messages_for_ctx(
            ctx,
            state_cache,
            client,
            inb_html,
            folder_type="inbox",
            own_coords=own_coords,
            priority=priority,
            always_persist_spy=True,
            delete_after_persist=delete_processed,
            on_transport_delivery_fn=on_transport_delivery_fn,
        )
        reports.extend(inb_rep)
        ingested_cnt += inb_cnt
        del_ok = del_ok or inb_del
        for ft_i, _hi, ui, ti in extract_message_folder_links(inb_post or inb_html):
            if ft_i:
                folder_counts[ft_i] = {"unread": int(ui), "total": int(ti)}
    return reports, ingested_cnt, del_ok, folder_counts


def _sweep_extra_message_folders(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient,
    folder_counts: dict[str, dict[str, int]],
    reports: list[SpyReport],
    *,
    own_coords: set[str],
    priority: str,
    delete_processed: bool,
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None,
) -> tuple[int, bool]:
    ingested_add = 0
    any_del = False
    for ftype in _EXTRA_MESSAGE_FOLDERS:
        ft_tot = folder_counts.get(ftype, {}).get("total", 0)
        if ft_tot <= 0 or ctx.rate_limit_remaining() > 0.0:
            continue
        for _pass in range(2 if ft_tot > 12 else 1):
            if ctx.rate_limit_remaining() > 0.0:
                break
            raw_f = call_with_optional_priority(client.open_messages, ftype, priority=priority)
            f_html = raw_f if isinstance(raw_f, str) else ""
            f_rep, f_ing, f_del, f_post = ingest_page_messages_for_ctx(
                ctx,
                state_cache,
                client,
                f_html,
                folder_type=ftype,
                own_coords=own_coords,
                priority=priority,
                always_persist_spy=True,
                delete_after_persist=delete_processed,
                on_transport_delivery_fn=on_transport_delivery_fn,
            )
            reports.extend(f_rep)
            ingested_add += f_ing
            any_del = any_del or f_del
            for ftu, _hu, uu, tu in extract_message_folder_links(f_post or f_html):
                if ftu == ftype:
                    folder_counts[ftype] = {"unread": int(uu), "total": int(tu)}
            if not f_del or folder_counts.get(ftype, {}).get("total", 0) <= 0:
                break
    return ingested_add, any_del


def ingest_messages_for_ctx(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient,
    state: GameState,
    *,
    include_folders: bool = True,
    delete_processed: bool = False,
    priority: str = "interactive",
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None = None,
    on_publish_snapshot: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Run interactive multi-folder message archival (`sync_messages` command implementation)."""
    call_with_optional_priority(client.ensure_in_game, priority=priority)
    url_now = extract_client_current_url(client)
    if url_now:
        ctx.last_url = url_now
    own_coords = own_planet_coords_for_ctx(ctx, state)
    reports, ingested_cnt, del_ok, folder_counts = _ingest_spio_and_inbox_pages(
        ctx,
        state_cache,
        client,
        own_coords=own_coords,
        priority=priority,
        delete_processed=delete_processed,
        on_transport_delivery_fn=on_transport_delivery_fn,
    )
    if include_folders:
        ex_ing, ex_del = _sweep_extra_message_folders(
            ctx,
            state_cache,
            client,
            folder_counts,
            reports,
            own_coords=own_coords,
            priority=priority,
            delete_processed=delete_processed,
            on_transport_delivery_fn=on_transport_delivery_fn,
        )
        ingested_cnt += ex_ing
        del_ok = del_ok or ex_del
    if del_ok:
        ctx.last_synced_unread_total = 0
    if ctx.last_state is not None:
        state_cache.refresh_cached_economy(ctx.last_state)
    if on_publish_snapshot is not None:
        on_publish_snapshot()
    return sanitize_json_dict(
        {
            "ok": True,
            "command": "sync_messages",
            "synced_reports": len(reports),
            "archived_messages": ingested_cnt,
            "deleted_in_game": del_ok,
            "folder_counts": folder_counts,
            "reports": redact_api_payload([asdict(r) for r in reports]),
        }
    )
