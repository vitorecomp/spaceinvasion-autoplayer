"""Page navigation (`navigate`) and inbox message sync (`sync_messages`) browser handlers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re
import time

from spaceinvasion.combat.fleet import (
    SpyReport,
    extract_galaxy_system_coords,
    extract_message_folder_links,
    normalize_coords,
    parse_galaxy_system_html,
)
from spaceinvasion.core import game_data
from spaceinvasion.core.state import (
    parse_daily_login_bonus,
    parse_economy_rates,
    parse_home_metadata,
    parse_universe_research_records,
)
from spaceinvasion.runtime.api.handlers._browser_proto import BrowserBridgeFacade
from spaceinvasion.runtime.api.handlers._browser_session import BrowserCommandSession, run_browser_command
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._helpers import (
    ALLOWED_MENU_ACTIONS,
    BRIDGE_RUNTIME_ERRORS,
    parse_command_int,
    redact_api_payload,
    sanitize_json_dict,
)
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.client import GameBrowserClient, RateLimitError, is_server_rate_limit_html
from spaceinvasion.session.executor import ActionExecutor


@dataclass(frozen=True)
class NavigateParams:
    """Parameters for `navigate`."""

    action: object = None
    galaxy: object = None
    system: object = None
    message_type: object = None
    building_detail_id: object = None
    include_html: bool = False
    return_home: bool = False


@dataclass(frozen=True)
class SyncMessagesParams:
    """Parameters for `sync_messages`."""


def _navigate_open_html(client: GameBrowserClient, action: str, params: NavigateParams) -> str:
    gal_int = parse_command_int("galaxy", params.galaxy, allow_none=True, min_val=1, max_val=1000)
    sys_int = parse_command_int("system", params.system, allow_none=True, min_val=1, max_val=1000)
    call_with_optional_priority(client.ensure_in_game, priority="interactive")
    if action == "internalGalaxy" and (gal_int is not None or sys_int is not None):
        return str(
            call_with_optional_priority(client.open_galaxy, galaxy=gal_int, system=sys_int, priority="interactive")
            or ""
        )
    if action == "internalMessages" and isinstance(params.message_type, str):
        return str(
            call_with_optional_priority(client.open_messages, params.message_type.strip(), priority="interactive") or ""
        )
    if action == "internalBuildings" and params.building_detail_id is not None:
        b_det_id = parse_command_int("building_detail_id", params.building_detail_id, min_val=1, max_val=99)
        assert b_det_id is not None
        return str(call_with_optional_priority(client.open_building_detail, b_det_id, priority="interactive") or "")
    try:
        return str(call_with_optional_priority(client.navigate_menu, action, priority="interactive") or "")
    except (RateLimitError, AntiBotChallengeError, SpaceInvasionAuthError):
        raise
    except BRIDGE_RUNTIME_ERRORS:
        if (
            action not in ("internalStatistics", "internalToplist", "internalUniverse", "internalSettings")
            or client.driver is None
        ):
            raise
        sid_q = str(client._resolve_sid_query() or "")
        q_str = f"action={action}" + (f"&{sid_q}" if sid_q else "")
        tgt_url = client._build_internal_url(q_str)
        tok = client._mark_frame_navigation("F_Hauptframe")
        client._switch_frame("F_Hauptframe")
        client.driver.execute_script("location.href = arguments[0];", tgt_url)
        return str(client._wait_frame_ready("F_Hauptframe", expected_action=action, nav_token=tok) or "")


def _detect_nav_observed_planet(bridge: BrowserBridgeFacade, action: str, html: str) -> int | None:
    ctx = bridge.ctx
    if not html:
        return None
    if action == "internalHome":
        low_h = html.lower()
        if not is_server_rate_limit_html(html) and "bitmeup-login" not in low_h and "sip-tab-universe" not in low_h:
            bridge.note_daily_login_bonus_seen_locked(present=bool(parse_daily_login_bonus(html).get("present", False)))
            return parse_home_metadata(html)[0]
        return None
    if action == "internalFleet" and ctx.last_state is not None:
        nav_fc = ActionExecutor._extract_internal_fleet_planet_coords(html)
        nav_fp = (
            next((p for p in ctx.last_state.planets if normalize_coords(p.coords) == nav_fc), None)
            if nav_fc
            else ctx.last_state.active_planet()
        )
        nav_slots = ActionExecutor._parse_internal_fleet_slot_status(html)
        if nav_slots is not None:
            bridge.record_observed_fleet_slots_locked(nav_fp, nav_slots[0])
            bridge.lifecycle.persist_runtime_state()
        return nav_fp.planet_id if (nav_fp is not None and nav_fc) else None
    if action in (
        "internalBuildings",
        "internalEconomy",
        "internalResearch",
        "internalWeaponFactory",
        "internalTransmitter",
    ):
        return GameBrowserClient._extract_selected_planet_id(html, action)
    return None


def _process_navigate_side_effects(bridge: BrowserBridgeFacade, action: str, html: str) -> None:
    ctx = bridge.ctx
    if html and action in ("internalStatistics", "internalToplist", "internalUniverse") and ctx.db is not None:
        nav_srv_recs = parse_universe_research_records(html)
        if nav_srv_recs:
            st_nav = ctx.last_state
            ctx.db.refresh_universe_research_max(
                own_researches=(st_nav.researches if st_nav is not None else None),
                own_nick=(st_nav.nickname if st_nav is not None else ""),
                server_records=nav_srv_recs,
                source=f"nav:{action}",
            )
            ctx.invalidate_dashboard_cache()
    obs_nav_pid = _detect_nav_observed_planet(bridge, action, html)
    handled_econ = False
    if action == "internalEconomy":
        rates = parse_economy_rates(html)
        if rates and ctx.last_state is not None:
            valid_pid = (
                obs_nav_pid
                if isinstance(obs_nav_pid, int) and any(p.planet_id == obs_nav_pid for p in ctx.last_state.planets)
                else None
            )
            active_p = ctx.last_state.active_planet()
            target_pid = valid_pid or (active_p.planet_id if active_p is not None else None)
            if target_pid is not None:
                ctx.economy_rates[target_pid] = dict(rates)
                ctx.economy_sync_timestamps[target_pid] = time.time()
                if ctx.db is not None:
                    p_coords = next((p.coords for p in ctx.last_state.planets if p.planet_id == target_pid), "")
                    ctx.db.save_economy_rates(target_pid, p_coords, rates)
                switched = valid_pid is not None and ctx.last_state.active_planet_id != valid_pid
                st_econ = (
                    replace(
                        ctx.last_state,
                        active_planet_id=valid_pid,
                        research_offers=() if len(ctx.last_state.planets) > 1 else ctx.last_state.research_offers,
                    )
                    if switched
                    else ctx.last_state
                )
                bridge.state_cache.record_state(
                    st_econ,
                    fetched_buildings=(ctx.last_buildings_fetched and not switched),
                    fetched_home=False,
                    observed_active_planet=(valid_pid is not None),
                    update_snapshot_ts=False,
                )
                handled_econ = True
    if (
        not handled_econ
        and ctx.last_state is not None
        and isinstance(obs_nav_pid, int)
        and any(p.planet_id == obs_nav_pid for p in ctx.last_state.planets)
    ):
        bridge.state_cache.sync_observed_active_planet_state(ctx.last_state, obs_pid=obs_nav_pid, obs_confirmed=True)


def _process_navigate_galaxy(
    bridge: BrowserBridgeFacade,
    client: GameBrowserClient,
    html: str,
    gal_int: int | None,
    sys_int: int | None,
) -> tuple[int, int, int]:
    ctx = bridge.ctx
    bridge.observe_galaxy_universe_bounds_locked(client=client, html=html)
    coords_attr = client.last_galaxy_coords
    if (
        isinstance(coords_attr, tuple)
        and len(coords_attr) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in coords_attr)
    ):
        actual_gal = max(1, min(1000, game_data.cap_json_int(coords_attr[0])))
        actual_sys = max(1, min(1000, game_data.cap_json_int(coords_attr[1])))
    else:
        def_gal, def_sys = gal_int or 1, sys_int or 1
        if gal_int is None and sys_int is None and ctx.last_state is not None:
            ap = ctx.last_state.active_planet()
            if ap is not None:
                m_ap = re.search(r"(\d{1,2}):(\d{1,3})", ap.coords, re.ASCII)
                if m_ap:
                    def_gal, def_sys = int(m_ap.group(1)), int(m_ap.group(2))
        actual_gal, actual_sys = extract_galaxy_system_coords(html, default=(def_gal, def_sys))
    slots: list[object] = list(parse_galaxy_system_html(html, galaxy=actual_gal, system=actual_sys))
    bridge.persist_galaxy_scan(actual_gal, actual_sys, slots)
    ctx.last_galaxy_browser_coords = (actual_gal, actual_sys)
    saved_slots = bridge.persist_galaxy_slots(slots)
    if ctx.last_state is not None:
        bridge.state_cache.refresh_cached_economy(ctx.last_state)
    return actual_gal, actual_sys, saved_slots


def cmd_navigate(bridge: BrowserBridgeFacade, params: NavigateParams) -> dict[str, object]:
    """Handle `navigate` command."""

    def _run(session: BrowserCommandSession) -> dict[str, object]:
        client = session.get_client()
        if not isinstance(params.action, str):
            raise ApiRequestError(f"Unsupported menu action; allowed: {sorted(ALLOWED_MENU_ACTIONS)}")
        action = params.action.strip()
        if action not in ALLOWED_MENU_ACTIONS:
            raise ApiRequestError(f"Unsupported menu action {action!r}; allowed: {sorted(ALLOWED_MENU_ACTIONS)}")
        gal_int = parse_command_int("galaxy", params.galaxy, allow_none=True, min_val=1, max_val=1000)
        sys_int = parse_command_int("system", params.system, allow_none=True, min_val=1, max_val=1000)
        html = _navigate_open_html(client, action, params)
        _process_navigate_side_effects(bridge, action, html)
        res_payload: dict[str, object] = {"ok": True, "command": "navigate", "action": action, "html_bytes": len(html)}
        if action == "internalGalaxy":
            actual_gal, actual_sys, saved_slots = _process_navigate_galaxy(bridge, client, html, gal_int, sys_int)
            res_payload["scanned_targets"] = saved_slots
            res_payload["galaxy"] = actual_gal
            res_payload["system"] = actual_sys
        if params.include_html:
            res_payload["html"] = redact_api_payload(html)
        if params.return_home:
            call_with_optional_priority(client.navigate_menu, "internalHome", priority="interactive")
        return sanitize_json_dict(res_payload)

    return run_browser_command(bridge, "navigate", {"action": params.action}, _run)


def _ingest_folder_passes(
    bridge: BrowserBridgeFacade,
    client: GameBrowserClient,
    *,
    ftype: str,
    own_coords: set[str],
    folder_counts: dict[str, dict[str, int]],
    reports: list[object],
) -> tuple[int, bool]:
    ctx = bridge.ctx
    ft_tot = folder_counts.get(ftype, {}).get("total", 0)
    if ft_tot <= 0 or ctx.rate_limit_remaining() > 0.0:
        return 0, False
    ingested_total = 0
    any_del = False
    for _pass in range(2 if ft_tot > 12 else 1):
        if ctx.rate_limit_remaining() > 0.0:
            break
        f_html = str(call_with_optional_priority(client.open_messages, ftype, priority="interactive") or "")
        f_rep, f_ing, f_del, f_post = bridge.ingest_and_delete_page_messages(
            client, f_html, folder_type=ftype, own_coords=own_coords, priority="interactive", always_persist_spy=True
        )
        reports.extend(f_rep)
        ingested_total += f_ing
        any_del = any_del or f_del
        for ftu, _hu, uu, tu in extract_message_folder_links(f_post or f_html):
            if ftu == ftype:
                folder_counts[ftype] = {"unread": int(uu), "total": int(tu)}
        if not f_del or folder_counts.get(ftype, {}).get("total", 0) <= 0:
            break
    return ingested_total, any_del


def cmd_sync_messages(bridge: BrowserBridgeFacade, _params: SyncMessagesParams) -> dict[str, object]:
    """Handle `sync_messages` command."""

    def _run(session: BrowserCommandSession) -> dict[str, object]:
        ctx = bridge.ctx
        client = session.get_client()
        call_with_optional_priority(client.ensure_in_game, priority="interactive")
        own_coords = bridge.own_planet_coords()
        html = str(call_with_optional_priority(client.open_messages, "spio", priority="interactive") or "")
        reports, ingested_cnt, del_ok, spio_post = bridge.ingest_and_delete_page_messages(
            client, html, folder_type="spio", own_coords=own_coords, priority="interactive", always_persist_spy=True
        )
        folder_links = extract_message_folder_links(spio_post or html)
        folder_counts: dict[str, dict[str, int]] = {
            ftype: {"unread": int(ucnt), "total": int(tot)} for ftype, _href, ucnt, tot in folder_links if ftype
        }
        if del_ok and folder_counts.get("spio", {}).get("total", 0) > 0 and ctx.rate_limit_remaining() <= 0.0:
            spio2 = str(call_with_optional_priority(client.open_messages, "spio", priority="interactive") or "")
            rep2, ing2, del2, post2 = bridge.ingest_and_delete_page_messages(
                client,
                spio2,
                folder_type="spio",
                own_coords=own_coords,
                priority="interactive",
                always_persist_spy=True,
            )
            reports.extend(rep2)
            ingested_cnt += ing2
            del_ok = del_ok or del2
            for ft2, _h2, u2, t2 in extract_message_folder_links(post2 or spio2):
                if ft2:
                    folder_counts[ft2] = {"unread": int(u2), "total": int(t2)}
        if not folder_links and not reports and ingested_cnt == 0:
            inb_html = str(call_with_optional_priority(client.open_messages, "", priority="interactive") or "")
            inb_rep, inb_cnt, inb_del, inb_post = bridge.ingest_and_delete_page_messages(
                client,
                inb_html,
                folder_type="inbox",
                own_coords=own_coords,
                priority="interactive",
                always_persist_spy=True,
            )
            reports.extend(inb_rep)
            ingested_cnt += inb_cnt
            del_ok = del_ok or inb_del
            for ft_i, _hi, ui, ti in extract_message_folder_links(inb_post or inb_html):
                if ft_i:
                    folder_counts[ft_i] = {"unread": int(ui), "total": int(ti)}
        for ftype in ("battle", "fleet", "normal", "alliance", "admin"):
            add_ing, add_del = _ingest_folder_passes(
                bridge, client, ftype=ftype, own_coords=own_coords, folder_counts=folder_counts, reports=reports
            )
            ingested_cnt += add_ing
            del_ok = del_ok or add_del
        if del_ok:
            ctx.last_synced_unread_total = 0
        if ctx.last_state is not None:
            bridge.state_cache.refresh_cached_economy(ctx.last_state)
        bridge.publish_snapshot()
        return sanitize_json_dict(
            {
                "ok": True,
                "command": "sync_messages",
                "synced_reports": len(reports),
                "archived_messages": ingested_cnt,
                "deleted_in_game": del_ok,
                "folder_counts": folder_counts,
                "reports": [asdict(r) for r in reports if isinstance(r, SpyReport)],
            }
        )

    return run_browser_command(bridge, "sync_messages", {}, _run)
