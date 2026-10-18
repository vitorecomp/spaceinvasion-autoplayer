"""Declarative Phase 4.1 command registry, lock-aware dispatcher, and docs generator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import MISSING, dataclass, fields, is_dataclass
from types import UnionType
from typing import Any, Generic, TypeVar, Union, get_args, get_origin, get_type_hints

from spaceinvasion.core.catalog.json_sanitize import sanitize_json_tree
from spaceinvasion.runtime.api.validation import (
    ApiRequestError,
    FieldBounds,
    coerce_params,
)

B = TypeVar("B")
P = TypeVar("P")
ResponseDict = dict[str, object]


@dataclass(frozen=True)
class CommandSpec(Generic[B]):
    """Metadata and callable binding for a single registered API command."""

    name: str
    handler: Callable[[B, Any], Mapping[str, object]]
    needs_browser: bool = False
    mutates: bool = False
    summary: str = ""
    params_type: type[object] | None = None


class CommandRegistry(Generic[B]):
    """Registry that validates parameters and acquires the browser lock only when needed."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec[B]] = {}

    def register(self, spec: CommandSpec[B]) -> CommandSpec[B]:
        """Register a `CommandSpec`, rejecting empty or duplicate command names."""
        key = spec.name.strip().lower()
        if not key or not key.isascii():
            raise ValueError(f"Invalid command name {spec.name!r}")
        if key in self._commands:
            raise ValueError(f"Duplicate command registration for {key!r}")
        if spec.params_type is not None and not is_dataclass(spec.params_type):
            raise TypeError(f"Command {key!r} params_type must be a dataclass")
        normalized = CommandSpec(
            name=key,
            handler=spec.handler,
            needs_browser=spec.needs_browser,
            mutates=spec.mutates,
            summary=spec.summary.strip(),
            params_type=spec.params_type,
        )
        self._commands[key] = normalized
        return normalized

    def command(
        self,
        name: str,
        *,
        needs_browser: bool = False,
        mutates: bool = False,
        summary: str = "",
        params: type[P] | None = None,
    ) -> Callable[[Callable[[B, P], Mapping[str, object]]], Callable[[B, P], Mapping[str, object]]]:
        """Decorator registering a command handler with declarative locking and param schema."""

        def _decorator(
            fn: Callable[[B, P], Mapping[str, object]],
        ) -> Callable[[B, P], Mapping[str, object]]:
            doc_summary = summary.strip() or (fn.__doc__.strip().splitlines()[0] if fn.__doc__ else "")
            self.register(
                CommandSpec(
                    name=name,
                    handler=fn,
                    needs_browser=needs_browser,
                    mutates=mutates,
                    summary=doc_summary,
                    params_type=params,
                )
            )
            return fn

        return _decorator

    def get(self, name: str) -> CommandSpec[B] | None:
        """Look up a registered command by normalized lowercase name."""
        return self._commands.get(name.strip().lower())

    def specs(self) -> tuple[CommandSpec[B], ...]:
        """Return all registered commands sorted deterministically by name."""
        return tuple(self._commands[k] for k in sorted(self._commands))

    def dispatch(
        self,
        bridge: B,
        payload: object,
        *,
        browser_lock: AbstractContextManager[object] | None = None,
    ) -> ResponseDict:
        """Validate command & params before locking, acquire browser lock only if needed, and return JSON."""
        if not isinstance(payload, Mapping):
            raise ApiRequestError("Request JSON must include a non-empty 'command' field.")
        raw_cmd = payload.get("command")
        if not isinstance(raw_cmd, str) or not raw_cmd.strip():
            raise ApiRequestError("Request JSON 'command' field must be a non-empty string.")
        cmd = raw_cmd.strip().lower()
        spec = self._commands.get(cmd)
        if spec is None:
            raise ApiRequestError(f"Unknown command {cmd!r}.", error_code="unknown_command")

        parsed_params: object = (
            coerce_params(spec.params_type, payload) if spec.params_type is not None else dict(payload)
        )
        lock_ctx: AbstractContextManager[object] = (
            browser_lock if (spec.needs_browser and browser_lock is not None) else nullcontext()
        )
        with lock_ctx:
            result = spec.handler(bridge, parsed_params)

        shaped: dict[str, object] = dict(result)
        shaped.setdefault("ok", True)
        shaped.setdefault("command", cmd)
        sanitized: dict[str, object] = sanitize_json_tree(shaped)
        return sanitized


def command(
    name: str,
    *,
    needs_browser: bool = False,
    mutates: bool = False,
    summary: str = "",
    params: type[P] | None = None,
    registry: CommandRegistry[B] | None = None,
) -> Callable[[Callable[[B, P], Mapping[str, object]]], Callable[[B, P], Mapping[str, object]]]:
    """Bind a handler to a `CommandRegistry` or attach metadata for later registration."""
    if registry is not None:
        return registry.command(
            name,
            needs_browser=needs_browser,
            mutates=mutates,
            summary=summary,
            params=params,
        )
    standalone: CommandRegistry[B] = CommandRegistry()
    return standalone.command(
        name,
        needs_browser=needs_browser,
        mutates=mutates,
        summary=summary,
        params=params,
    )


def _format_type_label(annotation: object) -> str:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        parts = [_format_type_label(arg) for arg in get_args(annotation)]
        return " | ".join(parts)
    if annotation is type(None):
        return "None"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _format_bounds_label(bounds: FieldBounds | None) -> str:
    if bounds is None:
        return "—"
    parts: list[str] = []
    if bounds.min_val is not None:
        parts.append(f"min={bounds.min_val}")
    if bounds.max_val is not None:
        parts.append(f"max={bounds.max_val}")
    if bounds.min_len is not None:
        parts.append(f"min_len={bounds.min_len}")
    if bounds.max_len is not None:
        parts.append(f"max_len={bounds.max_len}")
    if bounds.choices is not None:
        parts.append("choices=" + "/".join(bounds.choices))
    if not bounds.allow_empty:
        parts.append("non-empty")
    return ", ".join(parts) if parts else "—"


def generate_command_reference_markdown(registry: CommandRegistry[Any]) -> str:
    """Render a deterministic Markdown API command reference from a `CommandRegistry`."""
    specs = registry.specs()
    lines: list[str] = [
        "# SpaceInvasion Game API Command Reference",
        "",
        "Generated automatically from `CommandRegistry`.",
        "",
        "| Command | Browser Lock | Mutates | Parameters | Summary |",
        "| --- | :---: | :---: | --- | --- |",
    ]
    for spec in specs:
        param_name = spec.params_type.__name__ if spec.params_type is not None else "—"
        browser_txt = "Yes" if spec.needs_browser else "No"
        mutates_txt = "Yes" if spec.mutates else "No"
        summary_txt = spec.summary.replace("|", "\\|") if spec.summary else "—"
        lines.append(f"| `{spec.name}` | {browser_txt} | {mutates_txt} | `{param_name}` | {summary_txt} |")

    for spec in specs:
        if spec.params_type is None or not is_dataclass(spec.params_type):
            continue
        hints = get_type_hints(spec.params_type)
        lines.extend(
            [
                "",
                f"## `{spec.name}`",
                "",
                spec.summary or "No summary provided.",
                "",
                "| Parameter | Type | Required | Default | Constraints |",
                "| --- | --- | :---: | --- | --- |",
            ]
        )
        for dc_field in fields(spec.params_type):
            f_type = _format_type_label(hints.get(dc_field.name, dc_field.type))
            has_default = dc_field.default is not MISSING or dc_field.default_factory is not MISSING
            required_txt = "No" if has_default else "Yes"
            if dc_field.default is not MISSING:
                default_txt = f"`{dc_field.default!r}`"
            elif dc_field.default_factory is not MISSING:
                default_txt = "`<factory>`"
            else:
                default_txt = "—"
            raw_bounds = dc_field.metadata.get("bounds")
            bounds = raw_bounds if isinstance(raw_bounds, FieldBounds) else None
            lines.append(
                f"| `{dc_field.name}` | `{f_type}` | {required_txt} | {default_txt} | {_format_bounds_label(bounds)} |"
            )

    return "\n".join(lines) + "\n"
