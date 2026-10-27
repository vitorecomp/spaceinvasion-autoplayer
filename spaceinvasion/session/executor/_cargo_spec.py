"""Pure catalog-backed cargo capacity and transport ship selection for `exec_ops` (zero `combat` import)."""

from __future__ import annotations

from collections.abc import Mapping
import math

from spaceinvasion.core.catalog import SHIP_SLUG_BY_KEY, resolve_unit, safe_int, safe_key


def _safe_cargo_int(value: object, default: int = 0) -> int:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    return safe_int(value, default)


def fleet_cargo_capacity(ships: Mapping[str, int], *, miner_spec_level: int = 0) -> int:
    """Return total fleet cargo capacity in units across all non-zero ships."""
    if not isinstance(ships, Mapping):
        return 0
    total = 0
    for key, count in ships.items():
        cnt = _safe_cargo_int(count, 0)
        if cnt <= 0:
            continue
        key_s = safe_key(key)
        slug = SHIP_SLUG_BY_KEY.get(key_s, key_s)
        info = resolve_unit("ships", slug)
        if not info:
            continue
        stats = info.get("stats")
        cargo = _safe_cargo_int(stats.get("cargo", 0), 0) if isinstance(stats, Mapping) else 0
        total += cnt * cargo
    spec_lvl = min(100_000, max(0, _safe_cargo_int(miner_spec_level, 0)))
    if spec_lvl > 0:
        try:
            total = int(total * (1.0 + 0.05 * spec_lvl))
        except OverflowError:
            total = (total * (100 + 5 * spec_lvl)) // 100
    return total


def select_transport_ships(
    stationed_ships: Mapping[str, int],
    cargo_units: int,
    *,
    miner_spec_level: int = 0,
) -> dict[str, int]:
    """Select the minimal stationed cargo ship mix (`s3` -> `s2` -> `s1`) able to carry `cargo_units`."""
    if not isinstance(stationed_ships, Mapping) or cargo_units <= 0:
        return {}
    rem = int(cargo_units)
    chosen: dict[str, int] = {}
    for skey in ("s3", "s2", "s1"):
        avail = max(0, _safe_cargo_int(stationed_ships.get(skey, 0), 0))
        if avail <= 0 or rem <= 0:
            continue
        cap_one = fleet_cargo_capacity({skey: 1}, miner_spec_level=miner_spec_level)
        if cap_one <= 0:
            continue
        need_n = min(avail, (rem + cap_one - 1) // cap_one)
        if need_n > 0:
            chosen[skey] = need_n
            rem = max(0, rem - need_n * cap_one)
    return chosen
