"""Pure SpaceInvasion Classic flight timing, coordinate distance, station specs, and 3/24h Bash tracker."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
import math
import re
import time

from spaceinvasion.combat._bash_banners import parse_send_fleet_mission_block
from spaceinvasion.core.catalog import (
    SHIP_SLUG_BY_KEY,
    fmt_compact_int,
    resolve_unit,
    safe_finite_float,
    safe_int,
    safe_key,
)

__all__ = [
    "BashTracker",
    "DEFAULT_UNKNOWN_DEBRIS_RESOURCES",
    "ENGINE_RESEARCH_BY_NAME",
    "estimate_fleet_flight_seconds",
    "estimate_resource_per_hour",
    "fleet_effective_speed",
    "flight_distance_units",
    "format_flight_duration",
    "format_station_ships_spec",
    "normalize_coords",
    "parse_coords_distance",
    "parse_send_fleet_mission_block",
    "parse_station_ships_spec",
    "ship_effective_speed",
]

ENGINE_RESEARCH_BY_NAME: dict[str, str] = {
    "rocket engine": "r4",
    "diffusion engine": "r5",
    "warp engine": "r6",
}

DEFAULT_UNKNOWN_DEBRIS_RESOURCES: int = 25_000

_COORDS_NORM_RE = re.compile(
    r"^\s*\[?\s*(\d{1,2})\s*[:/\-]\s*(\d{1,3})\s*[:/\-]\s*(\d{1,2})\s*\]?\s*$",
    re.ASCII,
)

_STATION_SHIP_TOKEN_RE = re.compile(r"\b(\d{1,9})\s*x\s*(s\d{1,3})\b", re.IGNORECASE | re.ASCII)


def normalize_coords(coords: object) -> str:
    """Normalize SpaceInvasion coordinates like `'2:380:4'` to `'02:380:04'`."""
    if type(coords) is str and isinstance(coords, str):
        if (
            len(coords) == 9
            and coords[2] == ":"
            and coords[6] == ":"
            and coords[0:2].isascii()
            and coords[0:2].isdigit()
            and coords[3:6].isascii()
            and coords[3:6].isdigit()
            and coords[7:9].isascii()
            and coords[7:9].isdigit()
        ):
            return coords
        raw = coords.strip()
    else:
        raw = safe_key(coords).strip() if coords is not None else ""
    m = _COORDS_NORM_RE.match(raw)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):03d}:{int(m.group(3)):02d}"
    return raw


def _extract_coord_triple(raw: object) -> list[int]:
    txt = safe_key(raw) if raw is not None else ""
    return [min(999_999, int(tok[:9])) for tok in re.findall(r"\d+", txt, re.ASCII)[:3]]


def parse_coords_distance(a: object, b: object) -> float:
    """Heuristic coordinate distance used for raid & colony scoring."""
    pa = _extract_coord_triple(a)
    pb = _extract_coord_triple(b)
    if len(pa) < 3 or len(pb) < 3:
        return 100.0
    if pa[0] != pb[0]:
        return 20000.0 * abs(pa[0] - pb[0])
    if pa[1] != pb[1]:
        return 2700.0 + 95.0 * abs(pa[1] - pb[1])
    return 1000.0 + 5.0 * abs(pa[2] - pb[2])


def flight_distance_units(origin_coords: object, target_coords: object) -> float:
    """SpaceInvasion Classic linear flight distance calibrated to live RETRO timings."""
    pa = _extract_coord_triple(origin_coords)
    pb = _extract_coord_triple(target_coords)
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


def ship_effective_speed(
    ship_key: object,
    researches: Mapping[str, object] | None = None,
) -> float:
    """Return effective SpaceInvasion ship speed (`base_speed * (1 + 0.10 * engine_level)`)."""
    key_s = safe_key(ship_key)
    slug = SHIP_SLUG_BY_KEY.get(key_s, key_s)
    info = resolve_unit("ships", slug)
    if not info:
        return 0.0
    stats = info.get("stats")
    if not isinstance(stats, Mapping):
        return 0.0
    base_speed = float(max(0, safe_int(stats.get("speed", 0), 0)))
    if base_speed <= 0.0:
        return 0.0
    eng_name = str(stats.get("engine") or "").strip().lower()
    r_key = ENGINE_RESEARCH_BY_NAME.get(eng_name)
    eng_lvl = 0
    if r_key and isinstance(researches, Mapping):
        eng_lvl = min(10_000, max(0, safe_int(researches.get(r_key, 0), 0)))
    return base_speed * (1.0 + 0.10 * eng_lvl)


def fleet_effective_speed(
    ships: Mapping[str, int],
    researches: Mapping[str, object] | None = None,
) -> float:
    """Return the slowest mobile ship's effective speed in a multi-ship fleet."""
    if not isinstance(ships, Mapping):
        return 0.0
    slowest: float | None = None
    for key, count in ships.items():
        if safe_int(count, 0) <= 0:
            continue
        key_s = safe_key(key)
        if key_s in ("s9", "solar-cells"):
            continue
        spd = ship_effective_speed(key_s, researches)
        if spd > 0.0 and (slowest is None or spd < slowest):
            slowest = spd
    return float(slowest or 0.0)


def estimate_fleet_flight_seconds(
    origin_coords: object,
    target_coords: object,
    ships: Mapping[str, int],
    *,
    researches: Mapping[str, object] | None = None,
    round_trip: bool = True,
) -> float:
    """Estimate SpaceInvasion Classic flight duration in seconds (round-trip by default)."""
    speed = fleet_effective_speed(ships, researches)
    if speed <= 0.0:
        speed = 3000.0
    dist = flight_distance_units(origin_coords, target_coords)
    one_way_s = 10.0 + (4765.0 * dist) / max(1.0, speed)
    total_s = (2.0 * one_way_s) if round_trip else one_way_s
    return round(max(1.0, total_s), 2)


def estimate_resource_per_hour(total_resources: object, flight_seconds: object) -> float:
    """Return expected resource throughput per hour (`total_resources / (flight_seconds / 3600)`)."""
    res_f = safe_finite_float(total_resources, 0.0)
    sec_f = safe_finite_float(flight_seconds, 0.0)
    if res_f <= 0.0 or sec_f <= 0.0:
        return 0.0
    hours = max(sec_f / 3600.0, 1.0 / 3600.0)
    val = res_f / hours
    return round(val, 2) if math.isfinite(val) else 0.0


def format_flight_duration(seconds: object) -> str:
    """Format a flight duration in seconds into a compact human string (`48m`, `1h 12m`)."""
    sec_f = safe_finite_float(seconds, 0.0)
    if sec_f <= 0.0:
        return "0m"
    sec_i = int(round(sec_f))
    if sec_i < 60:
        return f"{sec_i}s"
    mins_total = (sec_i + 30) // 60
    if mins_total < 60:
        return f"{mins_total}m"
    hours, mins = divmod(mins_total, 60)
    if hours < 24:
        return f"{hours}h {mins:02d}m" if mins else f"{hours}h"
    days, rem_h = divmod(hours, 24)
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


def parse_station_ships_spec(text: str) -> dict[str, int]:
    """Extract `{ship_key: qty}` pairs from a formatted `STATION` label (e.g. `[15x s11, 6x s4]`)."""
    out: dict[str, int] = {}
    for m in _STATION_SHIP_TOKEN_RE.finditer(str(text or "")):
        qty = max(0, safe_int(m.group(1), 0))
        skey = m.group(2).lower()
        if qty > 0 and skey in SHIP_SLUG_BY_KEY and skey != "s9":
            out[skey] = out.get(skey, 0) + qty
    return out


def format_station_ships_spec(ships: Mapping[str, int]) -> str:
    """Format `{ship_key: qty}` into canonical `[Nx s11, Mx s4]` order."""
    parts = [
        f"{fmt_compact_int(int(qty))}x {skey}"
        for skey, qty in sorted(
            ships.items(),
            key=lambda kv: (int(kv[0][1:]) if kv[0].startswith("s") and kv[0][1:].isdigit() else 999, kv[0]),
        )
        if int(qty) > 0 and skey in SHIP_SLUG_BY_KEY and skey != "s9"
    ]
    return ", ".join(parts)


class BashTracker:
    """Enforce SpaceInvasion's 3-attacks-per-24h bash cap and server-enforced target blocks."""

    def __init__(self, *, max_active_attacks_24h: int = 3, war_max_24h: int = 5) -> None:
        self.max_active_attacks_24h = max_active_attacks_24h
        self.war_max_24h = war_max_24h
        self._history: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._block_reasons: dict[str, str] = {}

    @staticmethod
    def _norm_coords(target_coords: str) -> str:
        return normalize_coords(target_coords)

    @staticmethod
    def _resolve_ts(timestamp: float | None) -> float:
        if timestamp is None:
            return time.time()
        ts = safe_finite_float(timestamp, -1.0)
        return ts if ts >= 0.0 else time.time()

    def _prune(self, coords: str, now: float) -> None:
        dq = self._history[coords]
        cutoff = now - 86400.0
        while dq and dq[0] < cutoff:
            dq.popleft()

    def record_attack(self, target_coords: str, *, timestamp: float | None = None) -> None:
        now = self._resolve_ts(timestamp)
        key = self._norm_coords(target_coords)
        self._prune(key, now)
        self._history[key].append(now)

    def record_block(self, target_coords: str, blocked_until_ts: float, *, reason: str = "") -> None:
        key = self._norm_coords(target_coords)
        if not key:
            return
        until = safe_finite_float(blocked_until_ts, 0.0)
        if until <= 0.0:
            return
        if until >= self._blocked_until.get(key, 0.0):
            self._blocked_until[key] = until
            if reason:
                self._block_reasons[key] = safe_key(reason).strip()

    def blocked_until(self, target_coords: str, *, timestamp: float | None = None) -> float:
        now = self._resolve_ts(timestamp)
        key = self._norm_coords(target_coords)
        until = self._blocked_until.get(key, 0.0)
        if until <= now:
            if key in self._blocked_until:
                self._blocked_until.pop(key, None)
                self._block_reasons.pop(key, None)
            return 0.0
        return until

    def block_reason(self, target_coords: str, *, timestamp: float | None = None) -> str:
        if self.blocked_until(target_coords, timestamp=timestamp) <= 0.0:
            return ""
        return self._block_reasons.get(self._norm_coords(target_coords), "")

    def attacks_last_24h(self, target_coords: str, *, timestamp: float | None = None) -> int:
        now = self._resolve_ts(timestamp)
        key = self._norm_coords(target_coords)
        self._prune(key, now)
        return len(self._history[key])

    def can_attack(
        self,
        target_coords: str,
        *,
        is_inactive: bool = False,
        is_spacia: bool = False,
        in_declared_war: bool = False,
        timestamp: float | None = None,
    ) -> bool:
        if self.blocked_until(target_coords, timestamp=timestamp) > 0.0:
            return False
        if is_inactive or is_spacia:
            return True
        cap = self.war_max_24h if in_declared_war else self.max_active_attacks_24h
        return self.attacks_last_24h(target_coords, timestamp=timestamp) < cap
