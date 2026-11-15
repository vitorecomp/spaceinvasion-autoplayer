"""Galaxy slot status-color classification and DOM attribute extraction helpers."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from backend.parsing._common import attr_str, attr_tokens

_GALAXY_INACTIVE_TAG_RE = re.compile(r"\(\s*i+\s*\)", re.IGNORECASE)
_GALAXY_YELLOW_HEX_RE = re.compile(
    r"(?<![0-9a-f])#ff0(?![0-9a-f])|(?<![0-9a-f])#(?:ffff00|c9d324|d4af37|e6c200|cfc628|f0c000)(?![0-9a-f])|rgb\(\s*255\s*,\s*255\s*,\s*0\s*\)",
    re.IGNORECASE,
)
_GALAXY_RED_DARK_HEX_RE = re.compile(r"(?<![0-9a-f])#(?:8b0000|990000|800000)(?![0-9a-f])", re.IGNORECASE)
_GALAXY_GREEN_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:0f0|00ff00|008000|496c2a)(?![0-9a-f])|rgb\(\s*0\s*,\s*(?:255|128)\s*,\s*0\s*\)",
    re.IGNORECASE,
)
_GALAXY_BLUE_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:00f|0000ff|3399ff|347187|2b6a80|3b82a6)(?![0-9a-f])|rgb\(\s*0\s*,\s*0\s*,\s*255\s*\)",
    re.IGNORECASE,
)
_GALAXY_SRC_STATUS_RE = re.compile(
    r"(?:galaxy_blue|galaxy_white|galaxy_green|galaxy_purple|galaxy_yellow|galaxy_dark_red|galaxy_darkred|dark_red|darkred|dark-red|vacation|urlaub|newbie|noob|spectral|operator|inaktiv|inactive)",
    re.IGNORECASE,
)
_GALAXY_ATTR_BLUE_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:blue|vacation|urlaub|newbie|noob)(?![a-z0-9])", re.IGNORECASE
)
_GALAXY_ATTR_WHITE_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:white(?!\s*-\s*space)|spectral)(?![a-z0-9])", re.IGNORECASE
)
_GALAXY_ATTR_GREEN_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:green|alliance|ally|turquoise)(?![a-z0-9])", re.IGNORECASE
)
_GALAXY_ATTR_PURPLE_RE = re.compile(r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:purple|pink|operator)(?![a-z0-9])", re.IGNORECASE)
_GALAXY_ATTR_DARK_RED_RE = re.compile(r"(?:^|[^a-z0-9]|_)(?:galaxy_)?dark[_-]?red(?![a-z0-9])", re.IGNORECASE)
_GALAXY_ATTR_YELLOW_RE = re.compile(
    r"(?:^|[^a-z0-9]|_)(?:galaxy_)?(?:yellow|inaktiv|inactive)(?![a-z0-9])|color\s*:\s*yellow(?![a-z0-9])",
    re.IGNORECASE,
)
_GALAXY_TEXT_BLUE_RE = re.compile(r"\b(?:vacation|urlaub|newbie|noob)\b|\(\s*[vun]\s*\)", re.IGNORECASE)
_GALAXY_TEXT_WHITE_RE = re.compile(r"\bspectral\b|\(\s*s\s*\)", re.IGNORECASE)
_GALAXY_TEXT_PURPLE_RE = re.compile(r"\boperator\b|\(\s*go\s*\)", re.IGNORECASE)
_GALAXY_TEXT_DARK_RED_RE = re.compile(r"\btoo\s+strong\b", re.IGNORECASE)


def extract_slot_attr_blobs(container: Tag | BeautifulSoup | None) -> tuple[str, str]:
    """Collect lowered style/class/src attributes and title/alt semantic attributes from a slot container."""
    style_parts: list[str] = []
    semantic_parts: list[str] = []
    if container is None:
        return "", ""
    nodes: list[Tag] = [container] if isinstance(container, Tag) else []
    nodes.extend(el for el in container.find_all(True) if isinstance(el, Tag))
    for el in nodes:
        style_parts.extend(attr_tokens(el, "class"))
        for attr in ("style", "color", "data-status", "data-color", "data-state"):
            val = attr_str(el, attr)
            if val:
                style_parts.append(val)
        src = attr_str(el, "src")
        if src:
            style_parts.extend(_GALAXY_SRC_STATUS_RE.findall(src))
        for attr in ("title", "alt"):
            val = attr_str(el, attr)
            if val:
                semantic_parts.append(val)
    return " ".join(style_parts).lower(), " ".join(semantic_parts).lower()


def classify_slot_status(
    *,
    occupied: bool,
    is_spacia: bool,
    row_html: str,
    row_text: str,
    style_attrs: str | None = None,
    semantic_attrs: str | None = None,
) -> str:
    """Classify a galaxy row/card into SpaceInvasion status colors."""
    if not occupied:
        return "free"
    if is_spacia:
        return "yellow"
    if style_attrs is None or semantic_attrs is None:
        parsed_style, parsed_semantic = extract_slot_attr_blobs(BeautifulSoup(row_html or "", "html.parser"))
        style_attrs = parsed_style if style_attrs is None else style_attrs
        semantic_attrs = parsed_semantic if semantic_attrs is None else semantic_attrs
    semantic_text = f"{row_text or ''} {semantic_attrs}".lower()
    if (
        _GALAXY_ATTR_BLUE_RE.search(style_attrs)
        or _GALAXY_BLUE_HEX_RE.search(style_attrs)
        or _GALAXY_TEXT_BLUE_RE.search(semantic_text)
    ):
        return "blue"
    if _GALAXY_ATTR_WHITE_RE.search(style_attrs) or _GALAXY_TEXT_WHITE_RE.search(semantic_text):
        return "white"
    if _GALAXY_ATTR_GREEN_RE.search(style_attrs) or _GALAXY_GREEN_HEX_RE.search(style_attrs):
        return "green"
    if _GALAXY_ATTR_PURPLE_RE.search(style_attrs) or _GALAXY_TEXT_PURPLE_RE.search(semantic_text):
        return "purple"
    if (
        _GALAXY_ATTR_DARK_RED_RE.search(style_attrs)
        or _GALAXY_RED_DARK_HEX_RE.search(style_attrs)
        or _GALAXY_TEXT_DARK_RED_RE.search(semantic_text)
    ):
        return "dark_red"
    if (
        _GALAXY_ATTR_YELLOW_RE.search(style_attrs)
        or _GALAXY_YELLOW_HEX_RE.search(style_attrs)
        or _GALAXY_INACTIVE_TAG_RE.search(semantic_text)
    ):
        return "yellow"
    return "light_red"
