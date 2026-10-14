"""Parity, edge-case, and security-invariant tests for core redaction, coercion, and coords."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from spaceinvasion.combat import fleet as legacy_fleet
from spaceinvasion.core import coercion, coords, game_data, redaction
from spaceinvasion.core import config as legacy_config
from spaceinvasion.core import logging_utils as legacy_logging
from spaceinvasion.core import state as legacy_state
from spaceinvasion.db import backend as legacy_backend
from spaceinvasion.db import storage as legacy_storage
from spaceinvasion.runtime import api_server as legacy_api


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


def test_redaction_text_key_and_dsn_parity_with_legacy() -> None:
    """Verify exact equivalence with logging_utils, api_server, config, and db.backend."""
    key_samples = [
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
        "has_sid",
        "no_password",
        "is_secret",
        "enable_cookie",
        "planet_id",
        "s\u0131d",
        "\u017fid",
        "bsid",
        "",
    ]
    for key in key_samples:
        expected = legacy_logging.is_secret_dict_key(key)
        assert redaction.is_secret_dict_key(key) == expected
        assert legacy_config._is_cfg_secret_dict_key(key) == expected
        if key:
            assert redaction.is_clean_api_key(key) == legacy_api._is_clean_api_key(key)

    text_samples = [
        "ok",
        "planet_name=Homeworld&level=12",
        "https://game.example/index.es?action=internalHome&sid=ABC12345&p=2",
        '{"sid": "S_JSON", "has_sid": "false", "api_key": "K_JSON"}',
        "Authorization: Bearer TOK_XYZ_999",
        "Authorization: Basic dXNlcjpwYXNz",
        "Cookie: sid=CK_111; lang=en",
        "password='my secret pw'&ok=1",
        "sid=<ANGLE_TOK>&next=1",
        "sid=[BRACKET_TOK]&next=1",
        "sid=%22PCT_Q_TOK%22&next=1",
        '<input type="hidden" name="sid" value="INP_SID_9">',
        "has_sid=false&is_secret=1",
    ]
    for sample in text_samples:
        assert redaction.redact_text(sample) == legacy_logging.redact_sensitive(sample)
        assert redaction.redact_text(sample) == legacy_api._scrub_api_str(sample)
        if "<input" not in sample:
            assert redaction.redact_text(sample) == legacy_config._redact_cfg_text(sample)

    for dsn in (
        "postgresql://spaceinvasion:supersecret@127.0.0.1:5433/spaceinvasion",
        "postgresql://postgres:secret@/test_1?host=/tmp/pgsock",
        "postgresql:///test_1?host=/tmp/pgsock&user=postgres",
        "state/spaceinvasion.db",
        "/tmp/req?sid=DSN_PATH_SID",
    ):
        assert redaction.redact_db_url(dsn) == legacy_backend.redact_db_url(dsn)


def test_redaction_nested_containers_cycles_and_errors() -> None:
    """Verify nested dict/list/bytes/Path/exception/cycle scrubbing and clean error formatting."""
    deep_key: object = Path("?password=DEEP_PW_1")
    for _ in range(5):
        deep_key = (deep_key,)
    payload: dict[object, object] = {
        "url": "https://x/?sid=NEST_1",
        "sid": "DIRECT_1",
        "empty_sid": "",
        "password": None,
        "cookie": b"",
        "headers": {"Authorization": "Bearer HDR_1", "X-Ok": "clean"},
        "items": [("https://x/?sid=TUP_1", b"sid=BYTES_1"), Path("/tmp/p?sid=PATH_1")],
        "exc": RuntimeError(10**5000, "?password=EXC_PW_1"),
        "bad_fs": _RaiseFspath(),
        "int_fs": _IntPath(),
        deep_key: "ok",
    }
    payload["self"] = payload

    red_log = redaction.redact_sensitive(payload)
    red_api = redaction.redact_api_payload(payload)
    assert isinstance(red_log, dict) and isinstance(red_api, dict)
    assert red_log["self"] == "[Cycle]" and red_api["self"] == "[Cycle]"
    assert red_log["sid"] == "REDACTED" and red_api["sid"] == "REDACTED"
    assert red_log["password"] is None and red_log["cookie"] == ""
    assert red_api["bad_fs"] == "<unprintable>" and red_log["bad_fs"] == "<_RaiseFspath>"
    for dumped in (
        json.dumps({str(k): v for k, v in red_log.items()}),
        json.dumps({str(k): v for k, v in red_api.items()}),
    ):
        for secret in (
            "NEST_1",
            "DIRECT_1",
            "HDR_1",
            "TUP_1",
            "BYTES_1",
            "PATH_1",
            "EXC_PW_1",
            "DEEP_PW_1",
            "FS_SECRET_1",
        ):
            assert secret not in dumped

    wd_err = RuntimeError("Click intercepted at ?sid=WD_SID\n  (Session info: chrome=120)\nStacktrace:\n#0 0x123")
    assert redaction.format_clean_error(wd_err) == legacy_logging.format_clean_error(wd_err)
    assert "WD_SID" not in redaction.format_clean_error(wd_err)
    assert "Stacktrace" not in redaction.format_clean_error(wd_err)


def test_coercion_scalar_and_tree_parity() -> None:
    """Verify exact equivalence across legacy int/float/bool/ratio/JSON helpers."""
    edge_values: list[object] = [
        None,
        True,
        False,
        0,
        -0.0,
        1,
        -42,
        42.9,
        -3.7,
        float("nan"),
        float("inf"),
        float("-inf"),
        10**5000,
        -(10**5000),
        "",
        "   ",
        "0",
        "42",
        " -17 ",
        "+00089",
        "3.14",
        "1.234.567",
        "1_000",
        "9" * 5000,
        "-" + "9" * 5000,
        "²",
        "٣",
        "١٢٣",
        "null",
        "None",
        "~",
        "nan",
        "inf",
        "-infinity",
        b"123",
        bytearray(b"-55"),
        b"\xff\xfe",
        {"a": 1},
        [1, 2],
        (3,),
        _RaiseStr(),
    ]
    for val in edge_values:
        assert coercion.coerce_int(val, -9) == game_data.safe_int(val, -9)
        assert coercion.coerce_optional_int(val) == game_data.safe_optional_int(val, None)
        assert coercion.coerce_bounded_int(val, -9) == legacy_state._parse_bounded_int(val, -9)
        assert coercion.coerce_sql_int(val, -9) == legacy_storage._sql_int(val, -9)
        assert coercion.coerce_sql_optional_int(val) == legacy_storage._sql_optional_int(val)
        assert coercion.parse_game_int(val) == legacy_state.parse_si_int(val)
        assert coercion.cap_json_int(val) == game_data.cap_json_int(val)
        assert coercion.fmt_compact_int(val) == game_data.fmt_compact_int(val)
        assert coercion.coerce_bool(val, False) == legacy_config._safe_cfg_bool(val, False)
        assert coercion.coerce_bool(val, True) == legacy_config._safe_cfg_bool(val, True)
        got_f = coercion.coerce_finite_float(val, -1.5)
        assert got_f == game_data.safe_finite_float(val, -1.5) and math.isfinite(got_f)
        assert coercion.coerce_finite_float(val, -1.5, overflow_cap=None) == legacy_state._safe_state_float(val, -1.5)
        assert coercion.coerce_optional_finite_float(val) == legacy_state._safe_optional_state_float(val)

    neg_z = coercion.coerce_finite_float("-0.0", normalize_neg_zero=True)
    assert neg_z == 0.0 and math.copysign(1.0, neg_z) == 1.0
    assert coercion.safe_key((10**5000, Path("abc"))) == game_data.safe_key((10**5000, Path("abc")))
    cyc: dict[object, object] = {"big": 10**500, "bad_f": float("nan"), (1, 2): b"hi"}
    cyc["self"] = cyc
    assert coercion.sanitize_json_tree(cyc) == game_data.sanitize_json_tree(cyc)


def test_coercion_strict_api_validators_parity() -> None:
    """Verify strict ASCII int & finite float validators match legacy api_server validators."""
    strict_ints: list[object] = [
        None,
        True,
        False,
        0,
        42,
        -7,
        42.0,
        42.5,
        float("inf"),
        float("nan"),
        float(2**53),
        float(2**53 - 1),
        "42",
        "-17",
        "+007",
        "²",
        "٣",
        "1_000",
        "9" * 4001,
        "",
        [],
    ]
    for val in strict_ints:
        for allow_none in (False, True):
            legacy_ok = True
            legacy_val: int | None = None
            try:
                legacy_val = legacy_api._parse_command_int("x", val, allow_none=allow_none, min_val=-100, max_val=2**53)
            except legacy_api.ApiRequestError:
                legacy_ok = False
            if legacy_ok:
                assert (
                    coercion.parse_strict_ascii_int(val, name="x", allow_none=allow_none, min_val=-100, max_val=2**53)
                    == legacy_val
                )
            else:
                with pytest.raises(ValueError):
                    coercion.parse_strict_ascii_int(val, name="x", allow_none=allow_none, min_val=-100, max_val=2**53)

    for val in (None, True, 0, 1.5, "-0.0", "1_0", "٣", "nan", "inf", float("nan"), "", {}):
        legacy_ok = True
        legacy_f = 0.0
        try:
            legacy_f = legacy_api._validate_strict_float("f", val, min_val=-10.0, max_val=10.0)
        except legacy_api.ApiRequestError:
            legacy_ok = False
        if legacy_ok:
            assert coercion.parse_strict_finite_float(val, name="f", min_val=-10.0, max_val=10.0) == legacy_f
        else:
            with pytest.raises(ValueError):
                coercion.parse_strict_finite_float(val, name="f", min_val=-10.0, max_val=10.0)


def test_coords_parity_across_edge_cases() -> None:
    """Verify exact equivalence with legacy fleet, state, storage, and api_server coord helpers."""
    samples: list[object] = [
        None,
        "",
        "02:380:04",
        "2:380:4",
        "[3:120:7]",
        " 3 / 120 / 07 ",
        "3-120-7",
        "03:120:16",
        "03:120:17",
        "٣:١٢٠:٠٧",
        "²:120:7",
        "not-a-coord",
        "Attack -> [03:120:07]",
        12345,
        10**5000,
    ]
    for sample in samples:
        assert coords.normalize_coords(sample) == legacy_fleet.normalize_coords(sample)
        if isinstance(sample, str):
            assert coords.normalize_coord_triple(sample, strict_bounds=True) == legacy_fleet._norm_coord_triple(sample)
            assert coords.coord_query_variants(sample) == legacy_storage._coord_query_candidates(sample)
            assert coords.extract_coords_from_text(sample) == legacy_api._extract_coords_from_text(sample)

    parsed = coords.parse_coords("[2:38:4]", strict_bounds=True)
    assert parsed == coords.Coords(2, 38, 4)
    assert str(parsed) == "02:038:04" and coords.normalize_coords(parsed) == "02:038:04"
    assert coords.parse_coords("02:038:17", strict_bounds=True) is None
    assert coords.parse_coords("٣:١٢٠:٠٧") is None

    for pair in ("03:120", "3/400", "15:10", (3, 120), (True, 10), "٣:١٢٠", None):
        assert coords.parse_gal_sys_pair(pair) == legacy_fleet._parse_gal_sys_pair(pair)

    for orig, dest in (
        ("01:100:05", "01:100:05"),
        ("01:100:05", "01:100:12"),
        ("01:100:05", "01:115:05"),
        ("01:100:05", "03:100:05"),
        ("bad", "01:100:05"),
    ):
        assert coords.coords_distance(orig, dest) == legacy_fleet._parse_coords_distance(orig, dest)
        assert coords.flight_distance(orig, dest) == legacy_fleet._flight_distance_units(orig, dest)

    colonies = [(3, 120), (3, 395)]
    assert coords.galaxy_sector_distance(3, 120, colonies) == 0
    assert coords.galaxy_sector_distance(3, 5, colonies) == 10
    assert coords.galaxy_sector_distance(3, 125, colonies) == 5
    assert coords.galaxy_sector_distance(4, 120, colonies) == 250
    for a, b, exp in ((1, 400, (1, -1)), (400, 1, (1, 1)), (120, 120, (0, 0)), (10, 210, (200, 1)), (10, 211, (199, -1))):
        assert coords.circular_system_delta(a, b) == exp
