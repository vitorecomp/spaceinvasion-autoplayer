"""Behavioral, edge-case, and security-invariant tests for core redaction, coercion, and coords."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from backend.core import catalog as game_data
from backend.core import coercion, coords, redaction
from backend.core import config as cfg_mod


class _RaiseFspath:
    def __fspath__(self) -> str:
        raise RuntimeError("fspath boom ?password=FS_SECRET_1")


class _IntPath:
    def __fspath__(self) -> int:
        return 10**5000


class _RaiseStr:
    def __str__(self) -> str:
        raise RuntimeError("str boom")

    def __repr__(self) -> str:
        raise RuntimeError("repr boom")


def test_redaction_security_invariants_and_unicode_confusables() -> None:
    """Satisfies every case from tests/invariants/test_security_invariants.py plus confusables."""
    url_cases = [
        (
            "https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalHome&sid=SECRET_SID_9911&p=1",
            "SECRET_SID_9911",
        ),
        ("https://spaceinvasion.bitmeup.com/indexGame.es?SID=UPPER_SID_7722", "UPPER_SID_7722"),
        ("https://spaceinvasion.bitmeup.com/indexInternal.es?next=foo%3Fsid%3DENC_SID_4433%26ok=1", "ENC_SID_4433"),
        ("Cookie: sid=COOKIE_SID_5544; theme=dark", "COOKIE_SID_5544"),
        ("Authorization: Bearer BEARER_SECRET_6655", "BEARER_SECRET_6655"),
        ('<input type="hidden" name="sid" value="HTML_SID_1234">', "HTML_SID_1234"),
        ('{"password": "JSON_PW_1", "api_key": "JSON_KEY_2"}', "JSON_PW_1"),
        ("%22sid%22%3A%22URLENC_JSON_SID%22", "URLENC_JSON_SID"),
        ("\u017fid=LONGS_SID_999", "LONGS_SID_999"),
        ("s\u0131d=DOTLESS_I_SID_888", "DOTLESS_I_SID_888"),
        ("postgresql://spaceinvasion:SUPER_DB_PW@127.0.0.1:5433/spaceinvasion", "SUPER_DB_PW"),
    ]
    for raw, secret in url_cases:
        cleaned = redaction.redact_sensitive(raw)
        assert isinstance(cleaned, str)
        assert secret not in cleaned
        assert ("REDACTED" in cleaned) or ("***" in cleaned)
    assert redaction.redact_sensitive("has_sid=false&bsid=123") == "has_sid=false&bsid=123"
    assert redaction.redact_sensitive(RuntimeError("fail https://x/?sid=EXC_SID_1")) == "fail https://x/?sid=REDACTED"


def test_redaction_secret_keys_and_dsn_helpers() -> None:
    """Verify secret dict key classification and DSN password redaction."""
    secret_keys = (
        "sid",
        "SID",
        "password",
        "user_password",
        "db-passwd",
        "api_key",
        "x-api-key",
        "session_cookie",
        "client_secret",
        "authorization",
        "proxy_authorization",
        "s\u0131d",
        "\u017fid",
    )
    benign_keys = ("has_sid", "no_password", "is_secret", "enable_cookie", "planet_id", "bsid", "")
    for key in secret_keys:
        assert redaction.is_secret_dict_key(key) is True
    for key in benign_keys:
        assert redaction.is_secret_dict_key(key) is False

    cyclic: dict[str, object] = {
        "sid": "TOP_SECRET",
        "password": "",
        "empty_bytes": b"",
        "url": "https://game.example/?sid=URL_SECRET",
        "nested": [Path("/tmp/?sid=PATH_SECRET"), _RaiseFspath(), _RaiseStr()],
    }
    cyclic["self"] = cyclic
    cleaned = redaction.redact_sensitive(cyclic)
    assert isinstance(cleaned, dict)
    assert cleaned["sid"] == "REDACTED"
    assert cleaned["self"] == "[Cycle]"
    cleaned_json = json.dumps(coercion.sanitize_json_tree(cleaned), allow_nan=False)
    assert "TOP_SECRET" not in cleaned_json
    assert "URL_SECRET" not in cleaned_json
    assert "PATH_SECRET" not in cleaned_json
    assert "FS_SECRET_1" not in cleaned_json

    dsn = "postgresql://spaceinvasion:MY_PASS@127.0.0.1:5433/spaceinvasion"
    assert "MY_PASS" not in redaction.redact_db_url(dsn)
    assert "***" in redaction.redact_db_url(dsn)
    assert redaction.redact_db_url("postgresql:///test_db?host=/tmp/pgsock&user=postgres") == (
        "postgresql:///test_db?host=/tmp/pgsock&user=postgres"
    )
    assert "SECRET_ERR" not in redaction.redact_sensitive(RuntimeError("boom ?sid=SECRET_ERR\nStacktrace:\n#0 0x0"))


def test_coercion_numeric_and_json_tree_safety() -> None:
    """Verify bounded integer/float coercion, dot-grouped parsing, and finite JSON tree sanitization."""
    samples: list[object] = [
        None,
        True,
        False,
        0,
        -42,
        123456,
        10**5000,
        -(10**5000),
        3.9,
        float("nan"),
        float("inf"),
        float("-inf"),
        "",
        "   ",
        "null",
        "None",
        "~",
        "42",
        "-17",
        "+8",
        "3.14",
        "1_000",
        "1.234.567",
        "\u0661\u0665",
        "9" * 5000,
        "-" + "9" * 5000,
        b"123",
        b"\xff\xfe",
        [1],
        {"a": 1},
        {1},
        frozenset({1}),
        Path("42"),
        _RaiseFspath(),
        _IntPath(),
        _RaiseStr(),
    ]
    for val in samples:
        i_val = coercion.coerce_int(val, 7)
        assert isinstance(i_val, int) and not isinstance(i_val, bool)
        f_val = coercion.coerce_finite_float(val, 1.5)
        assert isinstance(f_val, float) and math.isfinite(f_val)
        c_val = coercion.coerce_bounded_int(val, 7)
        assert abs(c_val) <= coercion.MAX_JSON_SAFE_INT
        assert coercion.coerce_int(val, 7) == game_data.safe_int(val, 7)
        assert coercion.coerce_optional_int(val, None) == game_data.safe_optional_int(val, None)
        assert coercion.coerce_finite_float(val, 1.5) == game_data.safe_finite_float(val, 1.5)
        if not isinstance(val, os.PathLike):
            assert coercion.coerce_bounded_int(val, 7) == cfg_mod.safe_cfg_int(val, 7)
        assert coercion.coerce_bool(val, True) == cfg_mod.safe_cfg_bool(val, True)

    for raw_num, expected in (
        ("2.000", 2000),
        ("7.883.995.585.380", 7883995585380),
        ("", 0),
        ("\u0662.\u0660\u0660\u0660", 0),
    ):
        assert coercion.parse_si_int(raw_num) == expected
        assert coercion.parse_si_int(raw_num) == coercion.parse_game_int(raw_num)

    assert coercion.parse_strict_ascii_int("42", min_val=0, max_val=100) == 42
    with pytest.raises(ValueError):
        coercion.parse_strict_ascii_int(True)
    with pytest.raises(ValueError):
        coercion.parse_strict_ascii_int("\u0664\u0662")
    with pytest.raises(ValueError):
        coercion.parse_strict_ascii_int(1.5)

    assert coercion.parse_strict_finite_float("3.5", min_val=0.0, max_val=10.0) == pytest.approx(3.5)
    with pytest.raises(ValueError):
        coercion.parse_strict_finite_float(float("nan"))
    with pytest.raises(ValueError):
        coercion.parse_strict_finite_float("1_000.0")

    tree: dict[str, object] = {
        "ok": True,
        "nan": float("nan"),
        "inf": float("inf"),
        "huge": 10**5000,
        "path": _RaiseFspath(),
        "nested": (1, 2.5, float("-inf")),
    }
    tree["cycle"] = tree
    sanitized = coercion.sanitize_json_tree(tree)
    dumped = json.dumps(sanitized, allow_nan=False)
    assert '"cycle": "[Cycle]"' in dumped
    assert '"nan": 0.0' in dumped


def test_coords_normalization_parsing_variants_and_distance() -> None:
    """Verify coordinate normalization, query variants, and flight distance math."""
    assert coords.normalize_coords("3:120:7") == "03:120:07"
    assert coords.normalize_coords("[03/120-07]") == "03:120:07"
    assert coords.normalize_coords("invalid") == "invalid"
    assert coords.parse_coords("03:120:07") == coords.Coords(3, 120, 7)
    assert coords.parse_coords("bad") is None
    norm, variants, _like = coords.coord_query_variants("03:120:07")
    assert norm == "03:120:07"
    assert set(variants) >= {"03:120:07", "3:120:7"}
    assert coords.parse_gal_sys_pair("3:120") == (3, 120)
    assert coords.coords_distance("03:120:07", "03:120:07") == 1000.0
    assert coords.coords_distance("03:120:07", "03:121:07") > 1000.0
    assert coords.coords_distance("03:120:07", "04:120:07") > coords.coords_distance("03:120:07", "03:121:07")
