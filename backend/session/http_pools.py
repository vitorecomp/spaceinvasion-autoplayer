"""HTTP connection-pool disposal and safe session/response cleanup helpers."""

from __future__ import annotations

from collections.abc import Mapping

_SAFE_POOL_EXCS = (OSError, ValueError, TypeError, AttributeError, LookupError, RuntimeError)


def enable_pool_dispose(pm: object) -> None:
    """Attach a connection-pool close callback to prevent leaked socket warnings."""
    if pm is None or isinstance(pm, type):
        return
    pools = getattr(pm, "pools", None)
    if pools is None:
        return
    try:
        dispose = getattr(pools, "dispose_func", ...)
    except _SAFE_POOL_EXCS:
        return
    if dispose is None:
        try:
            pools.dispose_func = lambda p: p.close()
        except _SAFE_POOL_EXCS:
            pass


def configure_http_session_pools(sess: object) -> None:
    """Ensure urllib3 pool managers on a requests session dispose sockets on clear."""
    adapters = getattr(sess, "adapters", None)
    if not isinstance(adapters, Mapping):
        return
    for adapter in list(adapters.values()):
        enable_pool_dispose(getattr(adapter, "poolmanager", None))
        proxy_mgr = getattr(adapter, "proxy_manager", None)
        if isinstance(proxy_mgr, Mapping):
            for pm in list(proxy_mgr.values()):
                enable_pool_dispose(pm)


def close_http_session(sess: object) -> None:
    """Close an HTTP session after ensuring pool disposal hooks are attached."""
    if sess is None:
        return
    configure_http_session_pools(sess)
    close_fn = getattr(sess, "close", None)
    if callable(close_fn):
        close_fn()


def close_http_response(resp: object) -> None:
    """Close an HTTP response object when present."""
    if resp is None:
        return
    close_fn = getattr(resp, "close", None)
    if callable(close_fn):
        close_fn()
