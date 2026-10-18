"""Phase 4 API command registry and strict parameter validation package."""

from __future__ import annotations

from spaceinvasion.runtime.api.registry import (
    CommandRegistry,
    CommandSpec,
    command,
    generate_command_reference_markdown,
)
from spaceinvasion.runtime.api.validation import (
    ApiRequestError,
    FieldBounds,
    coerce_params,
    parse_strict_bool,
    parse_strict_float,
    parse_strict_int,
    parse_strict_str,
)

__all__ = [
    "ApiRequestError",
    "CommandRegistry",
    "CommandSpec",
    "FieldBounds",
    "coerce_params",
    "command",
    "generate_command_reference_markdown",
    "parse_strict_bool",
    "parse_strict_float",
    "parse_strict_int",
    "parse_strict_str",
]
