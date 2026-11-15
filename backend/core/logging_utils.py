"""Minimal compatibility shim for untouched ``notebooks/*.ipynb``."""

from __future__ import annotations

from backend.core.redaction import redact_sensitive as redact_sensitive

__all__ = ["redact_sensitive"]
