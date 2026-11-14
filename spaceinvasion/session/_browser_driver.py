"""ChromeDriver process/timeout management and Selenium runtime fallbacks."""

from __future__ import annotations

import ipaddress
import logging
import math
import os
from pathlib import Path
import socket
import subprocess
from typing import Any, Optional

from spaceinvasion.core.redaction import redact_sensitive

_LOGGER = logging.getLogger(__name__)


class _FallbackNoSuchElementError(RuntimeError):
    """Fallback exception when Selenium is not installed."""


class _FallbackWebDriverError(RuntimeError):
    """Fallback exception when Selenium is not installed."""


try:
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException, WebDriverException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    _SELENIUM_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded at runtime
    webdriver = None  # type: ignore[assignment]
    ChromeOptions = None  # type: ignore[assignment,misc]
    ChromeService = None  # type: ignore[assignment,misc]
    By = None  # type: ignore[assignment,misc]
    Keys = None  # type: ignore[assignment,misc]
    NoSuchElementException = _FallbackNoSuchElementError  # type: ignore[assignment,misc]
    WebDriverException = _FallbackWebDriverError  # type: ignore[assignment,misc]
    _SELENIUM_AVAILABLE = False


BROWSER_DOM_ERRORS: tuple[type[BaseException], ...] = (
    WebDriverException,
    NoSuchElementException,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    LookupError,
    TimeoutError,
)


def _prepare_profile_dir(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o700)
    if profile_dir.parent.name in (".browser_profile", "browser_profile"):
        os.chmod(profile_dir.parent, 0o700)


def _resolve_cdp_host(host: str, default_host: str = "127.0.0.1") -> str:
    """Resolve non-IP container hostnames (e.g. `browser`) to IPv4 so Chrome CDP accepts the `Host:` header."""
    raw = str(host or default_host).strip() or "127.0.0.1"
    if raw in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return raw
    try:
        ipaddress.ip_address(raw)
        return raw
    except ValueError:
        pass
    try:
        return socket.gethostbyname(raw)
    except OSError:
        return raw


def finite_bounded_timeout(
    raw: object,
    *,
    default: float,
    min_val: float = 0.05,
    max_val: float = 300.0,
) -> float:
    if raw is None or isinstance(raw, (bool, dict, list, tuple, set, frozenset)):
        return default
    coerced: object = bytes(raw).decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
    if isinstance(coerced, str):
        s = coerced.strip()
        if not s or not s.isascii():
            return default
        coerced = s
    if not isinstance(coerced, (int, float, str)):
        return default
    try:
        val = float(coerced)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(val):
        return default
    return max(min_val, min(max_val, val))


_finite_bounded_timeout = finite_bounded_timeout


_TERMINATE_GRACE_SECONDS: float = 5.0


def _terminate_or_kill_process(proc: Optional[subprocess.Popen[Any]]) -> None:
    """Terminate a child process and escalate to SIGKILL + wait so no orphan/ResourceWarning leaks."""
    if proc is None or proc.poll() is not None:
        return
    grace = max(0.05, float(_TERMINATE_GRACE_SECONDS))
    try:
        proc.terminate()
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError as exc:
            _LOGGER.debug("Process kill failed after terminate timeout: %s", exc)
        try:
            proc.wait(timeout=grace)
        except (subprocess.TimeoutExpired, OSError) as exc:
            _LOGGER.debug("Process wait failed after kill: %s", exc)
    except OSError:
        if proc.poll() is None:
            try:
                proc.wait(timeout=0.5)
            except (subprocess.TimeoutExpired, OSError) as exc:
                _LOGGER.debug("Process wait failed after OSError: %s", exc)


def safe_browser_err(exc: BaseException) -> str:
    res = redact_sensitive(exc)
    if isinstance(res, str):
        return res
    return f"<{type(exc).__name__}>"


_safe_browser_err = safe_browser_err


def configure_driver_timeouts(driver: Any, timeout: float) -> None:
    """Apply finite page-load, script, and WebDriver HTTP command timeouts."""
    bounded = finite_bounded_timeout(timeout, default=25.0, min_val=15.0, max_val=300.0)
    driver.set_page_load_timeout(bounded)
    set_script = getattr(driver, "set_script_timeout", None)
    if callable(set_script):
        try:
            set_script(bounded)
        except BROWSER_DOM_ERRORS as exc:
            _LOGGER.debug("set_script_timeout failed: %s", exc)
    executor = getattr(driver, "command_executor", None)
    if executor is not None and "unittest.mock" not in type(executor).__module__:
        cmd_timeout = max(20.0, min(60.0, bounded + 10.0))
        client_cfg = getattr(executor, "_client_config", None)
        if client_cfg is not None and hasattr(client_cfg, "timeout"):
            try:
                client_cfg.timeout = cmd_timeout
                old_conn = getattr(executor, "_conn", None)
                get_mgr = getattr(executor, "_get_connection_manager", None)
                if callable(get_mgr):
                    clear_fn = getattr(old_conn, "clear", None)
                    if old_conn is not None and callable(clear_fn):
                        try:
                            clear_fn()
                        except BROWSER_DOM_ERRORS as exc:
                            _LOGGER.debug("Failed clearing old urllib3 pool: %s", exc)
                    executor._conn = get_mgr()
            except BROWSER_DOM_ERRORS as exc:
                _LOGGER.debug("Failed applying command_executor timeout: %s", exc)


_configure_driver_timeouts = configure_driver_timeouts


def _close_driver_executor(driver: Any) -> None:
    """Release urllib3 pools on Selenium's command executor so sockets do not linger in CLOSE-WAIT."""
    executor = getattr(driver, "command_executor", None)
    if executor is None or "unittest.mock" in type(executor).__module__:
        return
    close_fn = getattr(executor, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except BROWSER_DOM_ERRORS as exc:
            _LOGGER.debug("command_executor.close() failed: %s", exc)


def _stop_service_process(service: Any) -> None:
    """Stop a ChromeDriver service and force-reap its child process if it ignores shutdown."""
    if service is None:
        return
    proc = getattr(service, "process", None)
    stop_fn = getattr(service, "stop", None)
    if callable(stop_fn):
        try:
            stop_fn()
        except BROWSER_DOM_ERRORS as exc:
            _LOGGER.debug("ChromeService.stop() failed: %s", exc)
    if isinstance(proc, subprocess.Popen):
        _terminate_or_kill_process(proc)


def close_or_detach_driver(
    driver: Any,
    launched_proc: Optional[subprocess.Popen[Any]] = None,
    *,
    keep_running: bool,
) -> None:
    """Detach from Chrome (leaving it open) or shut down WebDriver and Chrome."""
    if keep_running:
        _close_driver_executor(driver)
        _stop_service_process(getattr(driver, "service", None))
        return

    service = getattr(driver, "service", None)
    service_proc = getattr(service, "process", None)
    try:
        driver.quit()
    except BROWSER_DOM_ERRORS as exc:
        _LOGGER.debug("driver.quit() failed during cleanup: %s", exc)
    _close_driver_executor(driver)
    if isinstance(service_proc, subprocess.Popen):
        _terminate_or_kill_process(service_proc)
    _terminate_or_kill_process(launched_proc)


__all__ = [
    "BROWSER_DOM_ERRORS",
    "By",
    "ChromeOptions",
    "ChromeService",
    "Keys",
    "NoSuchElementException",
    "WebDriverException",
    "_SELENIUM_AVAILABLE",
    "_close_driver_executor",
    "_configure_driver_timeouts",
    "_finite_bounded_timeout",
    "_prepare_profile_dir",
    "_resolve_cdp_host",
    "_safe_browser_err",
    "_stop_service_process",
    "_terminate_or_kill_process",
    "close_or_detach_driver",
    "configure_driver_timeouts",
    "finite_bounded_timeout",
    "safe_browser_err",
    "webdriver",
]
