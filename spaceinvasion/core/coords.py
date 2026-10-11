"""Canonical SpaceInvasion Classic coordinate parsing, normalization, and distance math.

Stdlib-only foundation module (Phase 2.3 / Section 4 ``core``). Consolidates coordinate
normalization, SQL query variants, and flight/sector distance helpers previously
duplicated across ``combat/fleet.py``, ``core/state.py``, ``db/storage.py``, and
``runtime/api_server.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re

_COORDS_NORM_RE: re.Pattern[str] = re.compile(
    r"^\s*\[?\s*(\d{1,2})\s*[:/\-]\s*(\d{1,3})\s*[:/\-]\s*(\d{1,2})\s*\]?\s*$",
    re.ASCII,
)
_COORDS_SEARCH_RE: re.Pattern[str] = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)
_GAL_SYS_PAIR_RE: re.Pattern[str] = re.compile(r"(\d{1,2})\s*[:/\-]\s*(\d{1,3})", re.ASCII)
_FULL_GSP_RE: re.Pattern[str] = re.compile(r"(\d+):(\d+):(\d+)", re.ASCII)
_ASCII_DIGITS_RE: re.Pattern[str] = re.compile(r"\d+", re.ASCII)

_SAFE_TEXT_ERRORS = (TypeError, ValueError, RuntimeError, AttributeError, OverflowError, RecursionError)


@dataclass(frozen=True)
class Coords:
    """Parsed SpaceInvasion ``GG:SSS:PP`` planet coordinate."""

    galaxy: int
    system: int
    position: int

    def format(self) -> str:
        """Return canonical zero-padded ``GG:SSS:PP`` string."""
        return f"{self.galaxy:02d}:{self.system:03d}:{self.position:02d}"

    def as_tuple(self) -> tuple[int, int, int]:
        """Return ``(galaxy, system, position)``."""
        return (self.galaxy, self.system, self.position)

    def sector(self) -> tuple[int, int]:
        """Return ``(galaxy, system)``."""
        return (self.galaxy, self.system)

    def __str__(self) -> str:
        return self.format()


def _safe_coord_text(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, Coords):
        return raw.format()
    if type(raw) is str:
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, int) and not isinstance(raw, bool):
        limit = 10**200
        clamped = limit if raw > limit else (-limit if raw < -limit else raw)
        return str(clamped)
    try:
        return str(raw)
    except _SAFE_TEXT_ERRORS:
        return ""


def _is_canonical_coords_str(text: str) -> bool:
    return (
        len(text) == 9
        and text[2] == ":"
        and text[6] == ":"
        and text[0:2].isascii()
        and text[0:2].isdigit()
        and text[3:6].isascii()
        and text[3:6].isdigit()
        and text[7:9].isascii()
        and text[7:9].isdigit()
    )


def normalize_coords(coords: object) -> str:
    """Normalize SpaceInvasion coordinates like ``'2:380:4'`` to ``'02:380:04'``.

    Preserves non-coordinate strings stripped, matching legacy ``combat.fleet.normalize_coords``.
    Rejects non-ASCII Unicode digits (e.g. Arabic-Indic ``'٣'``).
    """
    if isinstance(coords, Coords):
        return coords.format()
    if type(coords) is str:
        if _is_canonical_coords_str(coords):
            return coords
        raw = coords.strip()
    else:
        raw = _safe_coord_text(coords).strip()
    match = _COORDS_NORM_RE.match(raw)
    if match:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):03d}:{int(match.group(3)):02d}"
    return raw


def parse_coords(raw: object, *, strict_bounds: bool = False) -> Coords | None:
    """Parse ``raw`` into a ``Coords`` instance, or ``None`` if invalid.

    When ``strict_bounds=True``, enforces SpaceInvasion bounds ``1 <= galaxy <= 99``,
    ``1 <= system <= 999``, and ``1 <= position <= 16``.
    """
    if isinstance(raw, Coords):
        cand = raw
    else:
        text = _safe_coord_text(raw).strip()
        match = _COORDS_NORM_RE.match(text)
        if not match:
            return None
        cand = Coords(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if strict_bounds and not (1 <= cand.galaxy <= 99 and 1 <= cand.system <= 999 and 1 <= cand.position <= 16):
        return None
    return cand


def normalize_coord_triple(raw: object, *, strict_bounds: bool = True) -> str:
    """Search ``raw`` for an inline ``G:S:P`` triple and return canonical ``GG:SSS:PP`` or ``''``.

    With ``strict_bounds=True`` (matching ``fleet._norm_coord_triple``), requires
    ``1 <= g <= 99``, ``1 <= s <= 999``, ``1 <= p <= 16``.
    With ``strict_bounds=False`` (matching ``state._normalize_queue_coords``), accepts any
    ``1-2:1-3:1-2`` ASCII triple.
    """
    if isinstance(raw, Coords):
        if strict_bounds and not (1 <= raw.galaxy <= 99 and 1 <= raw.system <= 999 and 1 <= raw.position <= 16):
            return ""
        return raw.format()
    text = _safe_coord_text(raw)
    match = _COORDS_SEARCH_RE.search(text)
    if not match:
        return ""
    g, s, p = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if strict_bounds and not (1 <= g <= 99 and 1 <= s <= 999 and 1 <= p <= 16):
        return ""
    return f"{g:02d}:{s:03d}:{p:02d}"


def extract_coords_from_text(text: object) -> str:
    """Extract and normalize the first inline ``G:S:P`` coordinate inside ``text``."""
    raw = _safe_coord_text(text)
    if not raw:
        return ""
    match = _COORDS_SEARCH_RE.search(raw)
    return normalize_coords(match.group(0)) if match else ""


def parse_gal_sys_pair(
    raw: object,
    *,
    max_galaxy: int = 14,
    max_system: int = 400,
) -> tuple[int, int] | None:
    """Parse ``(galaxy, system)`` from a 2-element sequence or string like ``'03:120'``."""
    if isinstance(raw, Coords):
        if 1 <= raw.galaxy <= max_galaxy and 1 <= raw.system <= max_system:
            return (raw.galaxy, raw.system)
        return None
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        g_raw, s_raw = raw[0], raw[1]
        if isinstance(g_raw, bool) or isinstance(s_raw, bool):
            return None
        try:
            g = int(g_raw)
            s = int(s_raw)
        except _SAFE_TEXT_ERRORS:
            return None
        return (g, s) if (1 <= g <= max_galaxy and 1 <= s <= max_system) else None
    text = _safe_coord_text(raw).strip()
    if not text:
        return None
    match = _GAL_SYS_PAIR_RE.search(text)
    if not match:
        return None
    g, s = int(match.group(1)), int(match.group(2))
    return (g, s) if (1 <= g <= max_galaxy and 1 <= s <= max_system) else None


def coord_query_variants(coords: object) -> tuple[str, list[str], str | None]:
    """Return ``(normalized, variant_list, like_pattern)`` for DB coordinate lookups."""
    raw = _safe_coord_text(coords).strip()
    norm = normalize_coords(raw)
    if not norm:
        return "", [], None
    match = _FULL_GSP_RE.fullmatch(norm)
    if not match:
        cands = [norm]
        if raw and raw != norm:
            cands.append(raw)
        return norm, cands, None
    g, s, p = int(match.group(1)[:9]), int(match.group(2)[:9]), int(match.group(3)[:9])
    variants: list[str] = []
    for candidate in (
        norm,
        raw,
        f"{g}:{s}:{p}",
        f"{g:02d}:{s}:{p}",
        f"{g}:{s:03d}:{p}",
        f"{g}:{s}:{p:02d}",
        f"{g:02d}:{s:03d}:{p}",
        f"{g:02d}:{s}:{p:02d}",
        f"{g}:{s:03d}:{p:02d}",
    ):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return norm, variants, f"%{g}%:%{s}%:%{p}%"


def _extract_three_parts(raw: object) -> list[int]:
    if isinstance(raw, Coords):
        return [min(999_999, raw.galaxy), min(999_999, raw.system), min(999_999, raw.position)]
    text = _safe_coord_text(raw)
    return [min(999_999, int(tok[:9])) for tok in _ASCII_DIGITS_RE.findall(text)[:3]]


def coords_distance(a: object, b: object) -> float:
    """Compute raid-scoring coordinate distance matching legacy ``_parse_coords_distance``."""
    pa = _extract_three_parts(a)
    pb = _extract_three_parts(b)
    if len(pa) < 3 or len(pb) < 3:
        return 100.0
    if pa[0] != pb[0]:
        return 20_000.0 * abs(pa[0] - pb[0])
    if pa[1] != pb[1]:
        return 2700.0 + 95.0 * abs(pa[1] - pb[1])
    return 1000.0 + 5.0 * abs(pa[2] - pb[2])


def flight_distance(origin: object, target: object) -> float:
    """Compute SpaceInvasion Classic linear flight distance calibrated to live RETRO timings."""
    pa = _extract_three_parts(origin)
    pb = _extract_three_parts(target)
    if len(pa) < 3 or len(pb) < 3:
        return 1000.0
    dg = abs(pa[0] - pb[0])
    if dg > 0:
        return 20_000.0 * dg
    ds = abs(pa[1] - pb[1])
    if ds > 0:
        return 1000.0 + 175.0 * ds
    dp = abs(pa[2] - pb[2])
    if dp > 0:
        return 1000.0 + 5.0 * dp
    return 500.0


def galaxy_sector_distance(
    galaxy: int,
    system: int,
    own_colonies: Sequence[tuple[int, int]],
    *,
    systems_per_galaxy: int = 400,
) -> int:
    """Return minimum circular system distance or cross-galaxy penalized distance."""
    n_sys = max(1, int(systems_per_galaxy))
    if not own_colonies:
        return 0
    best: int | None = None
    for col_g, col_s in own_colonies:
        delta = abs(int(system) - int(col_s))
        circ = min(delta, max(0, n_sys - delta))
        dist = circ if int(col_g) == int(galaxy) else (250 * abs(int(galaxy) - int(col_g)) + circ)
        if best is None or dist < best:
            best = dist
    return best if best is not None else 0


def circular_system_delta(
    from_sys: int,
    to_sys: int,
    *,
    systems_per_galaxy: int = 400,
) -> tuple[int, int]:
    """Return ``(shortest_circular_distance, direction)`` where ``direction`` is ``+1``, ``-1``, or ``0``."""
    n_sys = max(1, int(systems_per_galaxy))
    fwd = (int(to_sys) - int(from_sys)) % n_sys
    bwd = (int(from_sys) - int(to_sys)) % n_sys
    if fwd == 0:
        return 0, 0
    if fwd <= bwd:
        return fwd, 1
    return bwd, -1
