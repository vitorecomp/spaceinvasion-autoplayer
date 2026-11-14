"""Backwards-compatible private method delegates for `GameBrowserClient`."""

from __future__ import annotations

from spaceinvasion.session.client._state import ClientSessionState
from spaceinvasion.session.client.frames import FrameNavigator
from spaceinvasion.session.client.galaxy import GalaxyNavigator
from spaceinvasion.session.client.guards import PageGuardChecker
from spaceinvasion.session.client.reader import extract_selected_planet_id


class ClientCompatMixin:
    """Exposes legacy private helper methods on `GameBrowserClient` for runtime and test parity."""

    _state: ClientSessionState
    _guards: PageGuardChecker
    _frames: FrameNavigator
    _galaxy: GalaxyNavigator

    def _pace(self, *, priority: str = "normal", cost: int = 1) -> float:
        return self._state.pace(priority=priority, cost=cost)

    def _check_page_guards(self, html: str) -> None:
        self._guards.check_page_guards_impl(html)

    def _is_antibot_visible_in_live_dom(self) -> bool | None:
        return self._guards.is_antibot_visible_in_live_dom_impl()

    def _capture_dom_diag(self, html: str = "") -> dict[str, object]:
        return self._guards.capture_dom_diag(html)

    def _switch_frame(self, frame_id: str) -> None:
        self._frames.switch_frame_impl(frame_id)

    def _mark_frame_navigation(self, frame_id: str = "F_Hauptframe") -> str:
        return self._frames.mark_frame_navigation(frame_id)

    def _resolve_sid_query(self) -> str:
        return self._frames.resolve_sid_query_impl()

    def _build_internal_url(self, query: str) -> str:
        return self._frames.build_internal_url_impl(query)

    def _wait_frame_ready(
        self,
        frame_id: str,
        expected_action: str | None = None,
        *,
        nav_token: str | None = None,
    ) -> str:
        return self._frames.wait_frame_ready(frame_id, expected_action=expected_action, nav_token=nav_token)

    def _open_frame_url_with_retry(
        self,
        target_url: str,
        *,
        expected_action: str,
        frame_id: str = "F_Hauptframe",
    ) -> str:
        return self._frames.open_frame_url_with_retry_impl(
            target_url,
            expected_action=expected_action,
            frame_id=frame_id,
        )

    def _recover_renderer_timeout(self) -> None:
        self._frames.recover_renderer_timeout_impl()

    def _wait_galaxy_ajax_ready(self, html: str) -> str:
        return self._galaxy._dom.wait_galaxy_ajax_ready_impl(html)

    def _record_galaxy_universe_bounds(
        self,
        *,
        max_galaxies: object = None,
        max_system: object = None,
        html: str = "",
    ) -> tuple[int | None, int | None]:
        return self._galaxy._dom.record_universe_bounds_impl(
            max_galaxies=max_galaxies,
            max_system=max_system,
            html=html,
        )

    def _submit_galaxy_coords_form(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> str | None:
        return self._galaxy._dom.submit_galaxy_coords_form_impl(
            galaxy=galaxy,
            system=system,
            priority=priority,
        )

    def _sync_galaxy_json_textarea(self) -> None:
        self._galaxy._dom.sync_galaxy_json_textarea()

    def _finalize_galaxy_arrow_click(self, res: object, *, fallback_js: str, step_dir: int) -> str:
        return self._galaxy._dom._finalize_arrow_click(res, fallback_js=fallback_js, step_dir=step_dir)

    @staticmethod
    def extract_selected_planet_id(html: str, url: str = "") -> int | None:
        return extract_selected_planet_id(html, url)

    @staticmethod
    def _extract_selected_planet_id(html: str, url: str = "") -> int | None:
        return extract_selected_planet_id(html, url)
