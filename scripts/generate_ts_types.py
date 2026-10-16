#!/usr/bin/env python3
"""Deterministic Python-to-TypeScript API payload and settings schema generator.

Reflects over the canonical dataclass models in `spaceinvasion.runtime.api.payloads`
and the canonical strategy schema in `spaceinvasion.core.settings` to emit a
complete, deterministic TypeScript module (`--output <path>` / `--check <path>`).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import dataclasses
import difflib
import json
from pathlib import Path
import sys
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spaceinvasion.core.settings import export_frontend_settings_schema  # noqa: E402
from spaceinvasion.runtime.api.payloads import (  # noqa: E402
    PAGE_TITLES,
    PAYLOAD_MODELS,
    ActionGraphTabKey,
    FeedbackKind,
    PageKey,
)

_MODEL_NAMES: frozenset[str] = frozenset(cls.__name__ for cls in PAYLOAD_MODELS)


def _format_literal(arg: object) -> str:
    if isinstance(arg, str):
        return json.dumps(arg, ensure_ascii=False, allow_nan=False)
    if isinstance(arg, bool):
        return "true" if arg else "false"
    if isinstance(arg, (int, float)):
        return str(arg)
    if arg is None:
        return "null"
    raise TypeError(f"Unsupported Literal argument for TypeScript codegen: {arg!r}")


def _ts_type_from_annotation(annotation: object) -> str:
    """Convert a Python runtime type annotation into its TypeScript type expression."""
    if annotation is type(None) or annotation is None:
        return "null"
    if annotation is bool:
        return "boolean"
    if annotation in (int, float):
        return "number"
    if annotation is str:
        return "string"
    if annotation is object:
        return "any"
    if isinstance(annotation, type) and annotation.__name__ in _MODEL_NAMES:
        return annotation.__name__

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        return " | ".join(_format_literal(a) for a in args)

    if origin in (Union, UnionType):
        parts: list[str] = []
        seen: set[str] = set()
        for arg in args:
            rendered = _ts_type_from_annotation(arg)
            if rendered not in seen:
                seen.add(rendered)
                parts.append(rendered)
        return " | ".join(parts)

    if origin in (list, tuple, Sequence):
        elem = args[0] if args else object
        elem_ts = _ts_type_from_annotation(elem)
        if " | " in elem_ts or elem_ts.startswith("Record<"):
            return f"Array<{elem_ts}>"
        return f"{elem_ts}[]"

    if origin in (dict, Mapping):
        key_ts = _ts_type_from_annotation(args[0]) if len(args) >= 1 else "string"
        val_ts = _ts_type_from_annotation(args[1]) if len(args) >= 2 else "any"
        return f"Record<{key_ts}, {val_ts}>"

    raise TypeError(f"Unsupported annotation for TypeScript codegen: {annotation!r}")


def _emit_literal_type_alias(name: str, alias_type: object) -> list[str]:
    args = get_args(alias_type)
    if not args:
        raise ValueError(f"Expected Literal alias for {name}, got {alias_type!r}")
    if len(args) <= 4:
        joined = " | ".join(_format_literal(a) for a in args)
        return [f"export type {name} = {joined};", ""]
    lines = [f"export type {name} ="]
    for idx, arg in enumerate(args):
        suffix = ";" if idx == len(args) - 1 else ""
        lines.append(f"  | {_format_literal(arg)}{suffix}")
    lines.append("")
    return lines


def _emit_page_titles() -> list[str]:
    lines = ["export const PAGE_TITLES: Record<PageKey, string> = {"]
    for key, title in PAGE_TITLES.items():
        key_expr = key if key.isidentifier() else json.dumps(key, allow_nan=False)
        lines.append(f"  {key_expr}: {json.dumps(title, ensure_ascii=False, allow_nan=False)},")
    lines.extend(["};", ""])
    return lines


def _is_open_record(model: type[object]) -> bool:
    return bool(model.__dict__.get("_ts_open_record", False))


def _emit_interface(model: type[object]) -> list[str]:
    if not dataclasses.is_dataclass(model):
        raise TypeError(f"Expected dataclass model, got {model!r}")
    hints = get_type_hints(model)
    lines = [f"export interface {model.__name__} {{"]
    for fld in dataclasses.fields(model):
        ann = hints[fld.name]
        ts_type = _ts_type_from_annotation(ann)
        opt_marker = "?" if fld.metadata.get("optional") else ""
        lines.append(f"  {fld.name}{opt_marker}: {ts_type};")
    if _is_open_record(model):
        lines.append("  [key: string]: any;")
    lines.extend(["}", ""])
    return lines


def _emit_settings_schema_export() -> list[str]:
    schema = export_frontend_settings_schema()
    rendered_json = json.dumps(schema, indent=2, ensure_ascii=False, allow_nan=False)
    return [
        "export const GENERATED_SETTINGS_SCHEMA: SettingsSchemaPayload = " + rendered_json + ";",
        "",
    ]


def generate_ts_types_source() -> str:
    """Generate the complete deterministic TypeScript module text."""
    lines: list[str] = [
        "/* eslint-disable @typescript-eslint/no-explicit-any */",
        "// AUTO-GENERATED by scripts/generate_ts_types.py — DO NOT EDIT BY HAND.",
        "// Sources: spaceinvasion.runtime.api.payloads + spaceinvasion.core.settings",
        "",
    ]
    lines.extend(_emit_literal_type_alias("PageKey", PageKey))
    lines.extend(_emit_page_titles())
    lines.extend(_emit_literal_type_alias("ActionGraphTabKey", ActionGraphTabKey))
    lines.extend(_emit_literal_type_alias("FeedbackKind", FeedbackKind))
    for model in PAYLOAD_MODELS:
        lines.extend(_emit_interface(model))
    lines.extend(_emit_settings_schema_export())
    return "\n".join(lines).rstrip() + "\n"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or verify TypeScript API types and settings schema from Python sources."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="Write generated TypeScript module to this path.")
    mode.add_argument("--check", type=Path, help="Verify that this file matches generated TypeScript byte-for-byte.")
    mode.add_argument("--stdout", action="store_true", help="Write generated TypeScript module to stdout.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point supporting `--output`, `--check`, and `--stdout`."""
    args = _parse_args(argv)
    generated = generate_ts_types_source()

    if args.stdout:
        sys.stdout.write(generated)
        return 0

    if args.output is not None:
        target: Path = args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated, encoding="utf-8")
        sys.stdout.write(f"Wrote {len(generated)} bytes to {target}\n")
        return 0

    check_path: Path = args.check
    if not check_path.is_file():
        sys.stderr.write(f"[FAIL] Generated TypeScript target missing: {check_path}\n")
        return 1
    existing = check_path.read_text(encoding="utf-8")
    if existing == generated:
        sys.stdout.write(f"[PASS] TypeScript types in sync: {check_path}\n")
        return 0
    diff_lines = list(
        difflib.unified_diff(
            existing.splitlines(),
            generated.splitlines(),
            fromfile=str(check_path),
            tofile="generated",
            lineterm="",
        )
    )
    preview = "\n".join(diff_lines[:60])
    sys.stderr.write(f"[FAIL] TypeScript types out of sync with Python contracts ({check_path}):\n{preview}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
