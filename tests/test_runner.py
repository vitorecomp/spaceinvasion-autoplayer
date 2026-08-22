"""Unit tests for run_bot debug-port lifecycle and session recovery."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import run_bot
from spaceinvasion.auth import Credentials, SpaceInvasionAuthError


class TestRunnerDebugPortLifecycle(unittest.TestCase):
    def test_launches_detached_debug_browser_when_port_closed(self) -> None:
        creds = Credentials(email="pilot@example.com", password="secret", universe="RETRO")
        with (
            patch("run_bot.is_debug_port_open", return_value=False),
            patch("run_bot.load_credentials", return_value=creds),
            patch("run_bot.login_with_browser") as mock_login,
        ):
            run_bot.ensure_debug_browser_ready(
                debug_port=9222, env_file=Path(".env"), account="default"
            )
        mock_login.assert_called_once()
        cfg = mock_login.call_args.kwargs["config"]
        self.assertEqual(cfg.debug_port, 9222)
        self.assertTrue(cfg.detach)
        self.assertFalse(cfg.attach)

    def test_recovers_session_via_attach_when_kicked_out_of_game(self) -> None:
        creds = Credentials(email="pilot@example.com", password="secret", universe="RETRO")
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.ensure_in_game.side_effect = SpaceInvasionAuthError("not in game")

        with (
            patch("run_bot.is_debug_port_open", return_value=True),
            patch("run_bot.GameBrowserClient.attach", return_value=fake_client),
            patch("run_bot.load_credentials", return_value=creds),
            patch("run_bot.login_with_browser") as mock_login,
        ):
            run_bot.ensure_debug_browser_ready(
                debug_port=9222, env_file=Path(".env"), account="default"
            )
        mock_login.assert_called_once()
        cfg = mock_login.call_args.kwargs["config"]
        self.assertEqual(cfg.debug_port, 9222)
        self.assertTrue(cfg.attach)
        self.assertTrue(cfg.detach)


if __name__ == "__main__":
    unittest.main()
