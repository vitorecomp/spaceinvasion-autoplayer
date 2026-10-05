"""Typed parameter validation and coercion for Phase 4 API commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields, is_dataclass
import math
from types import UnionType
from typing import TypeVar, Union, get_args, get_origin, get_type_hints

P = TypeVar("P")

_MAX_INT_DIGITS = 4000
_MAX_SAFE_FLOAT_INT = (1 << 53) - 1


class ApiRequestError(ValueError):
    """Structured HTTP 400 client validation error matching the Game API contract."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_code: str = "bad_request",
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.field = field

    def to_error_payload(self) -> dict[str, object]:
        """Serialize into the canonical JSON error envelope returned by the HTTP bridge."""
        payload: dict[str, object] = {
            "ok": False,
            "error": str(self),
            "code": self.error_code,
            "status": self.status_code,
        }
        if self.field is not None:
            payload["field"] = self.field
        return payload


@dataclass(frozen=True)
class FieldBounds:
    """Declarative validation constraints attached via `field(metadata={'bounds': ...})`."""

    min_val: int | float | None = None
    max_val: int | float | None = None
    min_len: int | None = None
    max_len: int | None = None
    choices: tuple[str, ...] | None = None
    strip: bool = True
    allow_empty: bool = True
    description: str = ""


def parse_strict_int(
    name: str,
    val: object,
    *,
    allow_none: bool = False,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int | None:
    """Parse an integer strictly, rejecting booleans, Unicode digits, NaN/Inf, and bounds errors."""
    if allow_none and val is None:
        return None
    if isinstance(val, bool) or val is None or isinstance(val, (Mapping, list, tuple, set, frozenset)):
        raise ApiRequestError(f"Command parameter {name!r} must be an integer.", field=name)
    if isinstance(val, int):
        parsed = val
    elif isinstance(val, float):
        if not math.isfinite(val) or not val.is_integer() or abs(val) > _MAX_SAFE_FLOAT_INT:
            raise ApiRequestError(
                f"Command parameter {name!r} must be a safe finite integer.",
                field=name,
            )
        parsed = int(val)
    elif isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii():
            raise ApiRequestError(f"Command parameter {name!r} must be an integer.", field=name)
        digits = txt[1:] if txt[0] in ("+", "-") else txt
        if not digits or not digits.isdigit():
            raise ApiRequestError(f"Command parameter {name!r} must be an integer.", field=name)
        trimmed = digits.lstrip("0") or "0"
        if len(trimmed) > _MAX_INT_DIGITS:
            raise ApiRequestError(
                f"Command parameter {name!r} integer is out of range.",
                field=name,
            )
        parsed = -int(trimmed) if txt[0] == "-" else int(trimmed)
    else:
        raise ApiRequestError(f"Command parameter {name!r} must be an integer.", field=name)

    if min_val is not None and parsed < min_val:
        raise ApiRequestError(f"Command parameter {name!r} must be >= {min_val}.", field=name)
    if max_val is not None and parsed > max_val:
        raise ApiRequestError(f"Command parameter {name!r} must be <= {max_val}.", field=name)
    return parsed


def parse_strict_float(
    name: str,
    val: object,
    *,
    allow_none: bool = False,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float | None:
    """Parse a finite float strictly, rejecting booleans, Unicode digits, underscores, and NaN/Inf."""
    if allow_none and val is None:
        return None
    if isinstance(val, bool) or val is None or isinstance(val, (Mapping, list, tuple, set, frozenset)):
        raise ApiRequestError(f"Command parameter {name!r} must be a finite number.", field=name)
    raw: int | float | str
    if isinstance(val, (int, float)):
        raw = val
    elif isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or "_" in txt:
            raise ApiRequestError(f"Command parameter {name!r} must be a finite number.", field=name)
        raw = txt
    else:
        raise ApiRequestError(f"Command parameter {name!r} must be a finite number.", field=name)

    try:
        parsed = float(raw)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ApiRequestError(
            f"Command parameter {name!r} must be a finite number.",
            field=name,
        ) from exc
    if not math.isfinite(parsed):
        raise ApiRequestError(f"Command parameter {name!r} must be a finite number.", field=name)
    if min_val is not None and parsed < min_val:
        raise ApiRequestError(f"Command parameter {name!r} must be >= {min_val}.", field=name)
    if max_val is not None and parsed > max_val:
        raise ApiRequestError(f"Command parameter {name!r} must be <= {max_val}.", field=name)
    return parsed


def parse_strict_bool(name: str, val: object, *, allow_none: bool = False) -> bool | None:
    """Parse a boolean strictly without silently treating arbitrary truthy strings as True."""
    if allow_none and val is None:
        return None
    if isinstance(val, bool):
        return val
    raise ApiRequestError(f"Command parameter {name!r} must be a boolean.", field=name)


def parse_strict_str(
    name: str,
    val: object,
    *,
    allow_none: bool = False,
    bounds: FieldBounds | None = None,
) -> str | None:
    """Parse a string parameter with optional stripping, length bounds, and enum choices."""
    if allow_none and val is None:
        return None
    if not isinstance(val, str):
        raise ApiRequestError(f"Command parameter {name!r} must be a string.", field=name)
    text = val.strip() if (bounds is None or bounds.strip) else val
    if bounds is not None:
        if not bounds.allow_empty and not text:
            raise ApiRequestError(f"Command parameter {name!r} must be a non-empty string.", field=name)
        if bounds.min_len is not None and len(text) < bounds.min_len:
            raise ApiRequestError(
                f"Command parameter {name!r} length must be >= {bounds.min_len}.",
                field=name,
            )
        if bounds.max_len is not None and len(text) > bounds.max_len:
            raise ApiRequestError(
                f"Command parameter {name!r} length must be <= {bounds.max_len}.",
                field=name,
            )
        if bounds.choices is not None and text not in bounds.choices:
            opts = ", ".join(repr(c) for c in bounds.choices)
            raise ApiRequestError(
                f"Command parameter {name!r} must be one of: {opts}.",
                field=name,
            )
    return text


def _unwrap_optional_type(annotation: object) -> tuple[object, bool]:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        args = get_args(annotation)
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1 and len(non_none) < len(args):
            return non_none[0], True
    return annotation, False


def _coerce_field_value(
    name: str,
    raw: object,
    annotation: object,
    bounds: FieldBounds | None,
) -> object:
    base_type, allow_none = _unwrap_optional_type(annotation)
    if base_type is bool:
        return parse_strict_bool(name, raw, allow_none=allow_none)
    if base_type is int:
        min_i = int(bounds.min_val) if bounds and bounds.min_val is not None else None
        max_i = int(bounds.max_val) if bounds and bounds.max_val is not None else None
        return parse_strict_int(name, raw, allow_none=allow_none, min_val=min_i, max_val=max_i)
    if base_type is float:
        min_f = float(bounds.min_val) if bounds and bounds.min_val is not None else None
        max_f = float(bounds.max_val) if bounds and bounds.max_val is not None else None
        return parse_strict_float(name, raw, allow_none=allow_none, min_val=min_f, max_val=max_f)
    if base_type is str:
        return parse_strict_str(name, raw, allow_none=allow_none, bounds=bounds)
    if allow_none and raw is None:
        return None
    origin = get_origin(base_type)
    if origin in (dict, Mapping) or base_type is dict:
        if not isinstance(raw, Mapping):
            raise ApiRequestError(f"Command parameter {name!r} must be a JSON object.", field=name)
        return dict(raw)
    if origin in (list, tuple) or base_type in (list, tuple):
        if not isinstance(raw, (list, tuple)):
            raise ApiRequestError(f"Command parameter {name!r} must be a JSON array.", field=name)
        return tuple(raw) if (origin is tuple or base_type is tuple) else list(raw)
    return raw


def coerce_params(params_cls: type[P], payload: Mapping[str, object]) -> P:
    """Validate and instantiate a typed dataclass parameter model from a raw command payload."""
    if not is_dataclass(params_cls):
        raise TypeError(f"params_cls must be a dataclass type, got {params_cls!r}")
    hints = get_type_hints(params_cls)
    kwargs: dict[str, object] = {}
    for dc_field in fields(params_cls):
        fname = dc_field.name
        raw_bounds = dc_field.metadata.get("bounds")
        bounds = raw_bounds if isinstance(raw_bounds, FieldBounds) else None
        if fname in payload:
            annotation = hints.get(fname, dc_field.type)
            kwargs[fname] = _coerce_field_value(fname, payload[fname], annotation, bounds)
        elif dc_field.default is not MISSING:
            kwargs[fname] = dc_field.default
        elif dc_field.default_factory is not MISSING:
            kwargs[fname] = dc_field.default_factory()
        else:
            raise ApiRequestError(
                f"Missing required command parameter {fname!r}.",
                error_code="missing_parameter",
                field=fname,
            )
    return params_cls(**kwargs)
