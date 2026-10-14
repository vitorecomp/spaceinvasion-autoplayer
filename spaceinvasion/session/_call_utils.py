"""Signature-aware `priority=` forwarding helper for Selenium client test doubles."""

from __future__ import annotations

from collections.abc import Callable
import inspect
from typing import TypeVar

R = TypeVar("R")


def call_with_optional_priority(
    fn: Callable[..., R],
    *args: object,
    priority: str = "normal",
    **kwargs: object,
) -> R:
    """Invoke `fn` passing `priority=...` only if its signature (or wrapped mock) accepts it."""
    call_kwargs = dict(kwargs)
    targets: list[object] = [fn]
    for attr in ("side_effect", "_mock_wraps"):
        wrapped = getattr(fn, attr, None)
        if callable(wrapped) and not isinstance(wrapped, type):
            targets.append(wrapped)
    accepts_priority = True
    for target in targets:
        if not callable(target):
            continue
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            accepts_priority = False
            break
        if "priority" not in sig.parameters and not any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            accepts_priority = False
            break
    if accepts_priority:
        call_kwargs["priority"] = priority
    return fn(*args, **call_kwargs)
