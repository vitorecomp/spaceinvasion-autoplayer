"""Unread spy-folder and secondary inbox auto-sync helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
import time

from spaceinvasion.core.model import GameState
from spaceinvasion.parsing import InboxMessage, SpyReport, extract_message_folder_links, parse_unread_banner_count
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_int
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority
from spaceinvasion.runtime.bridge._intel_messages import (
    ingest_page_messages_for_ctx,
    is_real_client_method,
    own_planet_coords_for_ctx,
)
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.client import GameBrowserClient, RateLimitError

_LOGGER = logging.getLogger(__name__)


def _sync_extra_unread_folders(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient,
    folder_links: Sequence[tuple[str, int]],
    *,
    own_coords: set[str],
    priority: str,
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None,
) -> tuple[int, bool, str]:
    ingested = 0
    any_del = False
    last_post = ""
    for ftype, _ucnt in list(folder_links)[:3]:
        if ctx.rate_limit_remaining() > 0.0:
            break
        f_html = call_with_optional_priority(client.open_messages, ftype, priority=priority)
        if isinstance(f_html, str):
            _, f_ing, f_del, f_post = ingest_page_messages_for_ctx(
                ctx,
                state_cache,
                client,
                f_html,
                folder_type=ftype,
                own_coords=own_coords,
                priority=priority,
                on_transport_delivery_fn=on_transport_delivery_fn,
            )
            ingested += f_ing
            if f_del:
                any_del = True
                last_post = f_post
    return ingested, any_del, last_post


def _sync_secondary_spio_folders(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient,
    *,
    html: str,
    spio_reports: Sequence[SpyReport],
    spio_ingested: int,
    last_post_html: str,
    unread_spio: int,
    unread_total: int,
    pending_ready: bool,
    own_coords: set[str],
    priority: str,
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None,
) -> tuple[int, bool, str]:
    spio_links = [
        (ft, uc)
        for ft, _hr, uc, _tot in extract_message_folder_links(last_post_html or html)
        if ft and ft != "spio" and uc > 0
    ]
    added_ing = 0
    any_del = False
    post_out = last_post_html
    try:
        if spio_links:
            return _sync_extra_unread_folders(
                ctx,
                state_cache,
                client,
                spio_links,
                own_coords=own_coords,
                priority=priority,
                on_transport_delivery_fn=on_transport_delivery_fn,
            )
        if not spio_reports and spio_ingested == 0 and unread_spio <= 0 and (unread_total > 0 or pending_ready):
            inb_raw = call_with_optional_priority(client.open_messages, "", priority=priority)
            if isinstance(inb_raw, str):
                _, inb_ing, inb_del, inb_post = ingest_page_messages_for_ctx(
                    ctx,
                    state_cache,
                    client,
                    inb_raw,
                    folder_type="inbox",
                    own_coords=own_coords,
                    priority=priority,
                    on_transport_delivery_fn=on_transport_delivery_fn,
                )
                added_ing += inb_ing
                any_del = inb_del
                post_out = inb_post or post_out
                if inb_ing == 0:
                    inb_links = [
                        (ft, uc)
                        for ft, _hr, uc, _tot in extract_message_folder_links(inb_raw)
                        if ft and ft != "spio" and uc > 0
                    ]
                    ex_ing, ex_del, ex_post = _sync_extra_unread_folders(
                        ctx,
                        state_cache,
                        client,
                        inb_links,
                        own_coords=own_coords,
                        priority=priority,
                        on_transport_delivery_fn=on_transport_delivery_fn,
                    )
                    added_ing += ex_ing
                    any_del = any_del or ex_del
                    post_out = ex_post or post_out
    except (RateLimitError, AntiBotChallengeError):
        raise
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Secondary unread folder sync failed", exc_info=True)
    return added_ing, any_del, post_out


def sync_spio_if_unread_for_ctx(
    ctx: BridgeContext,
    state_cache: StateCacheService,
    client: GameBrowserClient | None,
    state: GameState,
    *,
    force: bool = False,
    priority: str = "normal",
    get_client: Callable[[], GameBrowserClient] | None = None,
    ignore_auth_error: bool = False,
    on_transport_delivery_fn: Callable[[InboxMessage], None] | None = None,
    on_persist_runtime_state: Callable[[], None] | None = None,
) -> int:
    """Archive unread spy and combat messages when badges or pending spy dispatches indicate work."""
    unread_map = state.unread_messages or {}
    unread_spio = safe_api_int(unread_map.get("spio", 0))
    unread_total = safe_api_int(unread_map.get("total", 0))
    now = time.monotonic()
    pending_ready = ctx.pending_spy_sync and (now - ctx.last_spy_dispatched_at) >= 8.0
    new_inbox_unread = unread_total > ctx.last_synced_unread_total
    if not force and unread_spio <= 0 and not pending_ready and not new_inbox_unread:
        if unread_total < ctx.last_synced_unread_total:
            ctx.last_synced_unread_total = max(0, unread_total)
        return 0
    if ctx.rate_limit_remaining() > 0.0:
        return 0
    if not force and (now - ctx.last_spio_sync_at) < BridgeConstants.SPIO_SYNC_TTL_S:
        return 0
    if client is None and get_client is not None:
        try:
            client = get_client()
        except (RateLimitError, AntiBotChallengeError):
            raise
        except SpaceInvasionAuthError:
            if ignore_auth_error:
                return 0
            raise
    if client is None or not is_real_client_method(client, "open_messages"):
        return 0
    ctx.last_spio_sync_at = now
    own_coords = own_planet_coords_for_ctx(ctx, state)
    try:
        raw_html = call_with_optional_priority(client.open_messages, "spio", priority=priority)
        html = raw_html if isinstance(raw_html, str) else ""
    except (RateLimitError, AntiBotChallengeError):
        raise
    except BRIDGE_RUNTIME_ERRORS:
        _LOGGER.debug("Failed to open spio messages page", exc_info=True)
        return 0

    spio_reports, total_ingested, any_deleted, last_post_html = ingest_page_messages_for_ctx(
        ctx,
        state_cache,
        client,
        html,
        folder_type="spio",
        own_coords=own_coords,
        priority=priority,
        on_transport_delivery_fn=on_transport_delivery_fn,
    )
    sec_ing, sec_del, last_post_html = _sync_secondary_spio_folders(
        ctx,
        state_cache,
        client,
        html=html,
        spio_reports=spio_reports,
        spio_ingested=total_ingested,
        last_post_html=last_post_html,
        unread_spio=unread_spio,
        unread_total=unread_total,
        pending_ready=pending_ready,
        own_coords=own_coords,
        priority=priority,
        on_transport_delivery_fn=on_transport_delivery_fn,
    )
    total_ingested += sec_ing
    any_deleted = any_deleted or sec_del

    ctx.pending_spy_sync = False
    if any_deleted:
        ctx.last_synced_unread_total = 0
        if last_post_html:
            _ = parse_unread_banner_count(last_post_html)
    else:
        ctx.last_synced_unread_total = max(0, unread_total)
    if on_persist_runtime_state is not None:
        on_persist_runtime_state()
    return total_ingested
