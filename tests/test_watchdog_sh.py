"""Unit tests for `run_stack.sh` crash-restart supervisor."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_STACK_SH = REPO_ROOT / "run_stack.sh"


class TestWatchdogScript(unittest.TestCase):
    def _base_env(self, tmp: Path, fake_stack: Path) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "PYTHON_BIN": sys.executable,
                "STACK_SCRIPT": str(fake_stack),
                "WATCHDOG_LOG": str(tmp / "watchdog.log"),
                "WATCHDOG_PID_FILE": str(tmp / "watchdog.pid"),
                "INITIAL_BACKOFF_SEC": "0.1",
                "MAX_BACKOFF_SEC": "0.2",
                "HEALTHY_UPTIME_RESET_SEC": "60",
                "MAX_RAPID_RESTARTS": "0",
            }
        )
        return env

    def test_restarts_after_crashes_and_stops_on_clean_exit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            count_file = tmp / "runs.txt"
            fake_stack = tmp / "fake_stack.py"
            fake_stack.write_text(
                "import pathlib, sys\n"
                f"p = pathlib.Path({str(count_file)!r})\n"
                "lines = p.read_text().splitlines() if p.exists() else []\n"
                "lines.append(' '.join(sys.argv[1:]))\n"
                "p.write_text('\\n'.join(lines) + '\\n')\n"
                "sys.exit(1 if len(lines) < 3 else 0)\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                ["bash", str(RUN_STACK_SH)],
                env=self._base_env(tmp, fake_stack),
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            invocations = count_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(invocations, ["--live", "--live", "--live"])
            log_txt = (tmp / "watchdog.log").read_text(encoding="utf-8")
            self.assertIn("Stack crashed with exit code 1", log_txt)
            self.assertIn("Stack exited cleanly (code 0", log_txt)
            self.assertFalse((tmp / "watchdog.pid").exists())

    def test_stops_immediately_on_antibot_exit_code_2_without_restarting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            count_file = tmp / "runs.txt"
            fake_stack = tmp / "fake_stack.py"
            fake_stack.write_text(
                "import pathlib, sys\n"
                f"p = pathlib.Path({str(count_file)!r})\n"
                "n = int(p.read_text()) if p.exists() else 0\n"
                "p.write_text(str(n + 1))\n"
                "sys.exit(2)\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                ["bash", str(RUN_STACK_SH), "--dry-run"],
                env=self._base_env(tmp, fake_stack),
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            self.assertEqual(res.returncode, 2)
            self.assertEqual(count_file.read_text(encoding="utf-8"), "1")
            self.assertIn("Anti-bot / CAPTCHA challenge detected", res.stdout)

    def test_sigterm_stops_child_cleanly_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ready_file = tmp / "ready.flag"
            fake_stack = tmp / "fake_stack.py"
            fake_stack.write_text(
                "import pathlib, signal, sys, time\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                f"pathlib.Path({str(ready_file)!r}).write_text('ok')\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            proc = subprocess.Popen(
                ["bash", str(RUN_STACK_SH), "--dry-run"],
                env=self._base_env(tmp, fake_stack),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not ready_file.exists():
                    time.sleep(0.05)
                self.assertTrue(ready_file.exists())
                proc.send_signal(signal.SIGTERM)
                out, err = proc.communicate(timeout=8.0)
            finally:
                if proc.poll() is None:
                    proc.kill()
            self.assertEqual(proc.returncode, 0, msg=err)
            self.assertIn("Stop signal received", out)
            self.assertFalse((tmp / "watchdog.pid").exists())

    def test_help_prints_usage_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake_stack = tmp / "fake_stack.py"
            fake_stack.write_text(
                "import sys\nprint('FAKE_STACK_HELP', sys.argv[1:])\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                ["bash", str(RUN_STACK_SH), "--help"],
                env=self._base_env(tmp, fake_stack),
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("Usage: ./run_stack.sh", res.stdout)
            self.assertIn("FAKE_STACK_HELP ['--help']", res.stdout)


if __name__ == "__main__":
    unittest.main()
