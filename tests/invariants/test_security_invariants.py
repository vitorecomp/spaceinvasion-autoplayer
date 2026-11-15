"""Core security & secret-redaction invariants.

Tagged ``@pytest.mark.invariants``. Ensures ``redact_sensitive`` scrubs transient
``sid=<token>`` values across URLs, headers, nested containers, and exceptions.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import pytest

from backend.core.redaction import redact_sensitive

pytestmark = pytest.mark.invariants


@pytest.mark.invariants
class TestSecurityInvariants(unittest.TestCase):
    """``redact_sensitive`` must scrub ``sid=<token>`` across all log/error surfaces."""

    def test_redact_sensitive_scrubs_sid_in_urls_and_headers(self) -> None:
        url_cases = [
            (
                "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=SECRET_SID_9911&p=1",
                "SECRET_SID_9911",
            ),
            ("https://spaceinvasion.bitmeup.com/indexGame.es?SID=UPPER_SID_7722", "UPPER_SID_7722"),
            ("https://spaceinvasion.bitmeup.com/indexInternal.es?next=foo%3Fsid%3DENC_SID_4433%26ok=1", "ENC_SID_4433"),
            ("Cookie: sid=COOKIE_SID_5544; theme=dark", "COOKIE_SID_5544"),
            ("Authorization: Bearer BEARER_SECRET_6655", "BEARER_SECRET_6655"),
        ]
        for raw, secret in url_cases:
            cleaned = redact_sensitive(raw)
            self.assertNotIn(secret, cleaned, f"Secret {secret!r} leaked in redacted text: {cleaned!r}")
            self.assertIn("REDACTED", cleaned)

        # Preserve benign non-secret lookalike tokens.
        self.assertEqual(redact_sensitive("has_sid=false&bsid=123"), "has_sid=false&bsid=123")

    def test_redact_sensitive_scrubs_nested_dicts_sequences_bytes_and_paths(self) -> None:
        cyclic: dict[str, object] = {
            "url": "https://spaceinvasion.bitmeup.com/indexGame.es?sid=NESTED_SID_111",
            "headers": {
                "Cookie": "sid=HDR_SID_222; lang=en",
                "X-Trace": "ok",
            },
            "sid": "DIRECT_SID_333",
            "items": [
                ("https://example.com/?sid=TUPLE_SID_444", b"raw?sid=BYTES_SID_555"),
                Path("/tmp/req?sid=PATH_SID_666.html"),
            ],
            "sid=KEY_SID_777": "value",
        }
        cyclic["self"] = cyclic

        cleaned = redact_sensitive(cyclic)
        serialized = repr(cleaned)
        for leaked in (
            "NESTED_SID_111",
            "HDR_SID_222",
            "DIRECT_SID_333",
            "TUPLE_SID_444",
            "BYTES_SID_555",
            "PATH_SID_666",
            "KEY_SID_777",
        ):
            self.assertNotIn(leaked, serialized, f"Secret {leaked!r} leaked in nested redaction: {serialized}")
        self.assertEqual(cleaned["sid"], "REDACTED")
        self.assertEqual(cleaned["self"], "[Cycle]")
        self.assertEqual(cleaned["headers"]["X-Trace"], "ok")

    def test_redact_sensitive_scrubs_exception_messages(self) -> None:
        exc = RuntimeError(
            "Navigation failed at https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalBuildings&sid=EXC_SID_8899"
        )
        cleaned = redact_sensitive(exc)
        self.assertNotIn("EXC_SID_8899", cleaned)
        self.assertIn("sid=REDACTED", cleaned)
