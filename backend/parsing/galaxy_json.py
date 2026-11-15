"""2D Galaxy ``#json-element`` slot metadata parser and DOM enrichment helper."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json

from bs4 import BeautifulSoup, Tag

from backend.core.coercion import coerce_int, coerce_optional_int
from backend.parsing.models import GalaxySlot


@dataclass(frozen=True)
class JsonSlotMeta:
    c_min: int | None
    c_max: int | None
    uf_exact: int | None
    temperature: int | None
    is_free: bool
    planet_name: str
    owner: str
    has_debris: bool
    has_rubble_key: bool
    debris_pig_iron: int
    debris_metal: int
    debris_kryptonite: int
    debris_spice: int


def _parse_json_rubble(pdata: Mapping[object, object]) -> tuple[bool, bool, int, int, int, int]:
    has_rubble_key = False
    raw_rubble: object = None
    if "rubblefield1" in pdata:
        has_rubble_key = True
        raw_rubble = pdata.get("rubblefield1")
    else:
        for rk in ("rubblefield", "rubble", "debris", "tf"):
            if rk in pdata:
                has_rubble_key = True
                raw_rubble = pdata.get(rk)
                break
    d_pig = d_met = d_kry = d_spi = 0
    if isinstance(raw_rubble, Sequence) and not isinstance(raw_rubble, (str, bytes)):
        vals4 = [max(0, coerce_int(x, 0)) for x in raw_rubble[:4]]
        while len(vals4) < 4:
            vals4.append(0)
        d_pig, d_met, d_kry, d_spi = vals4[0], vals4[1], vals4[2], vals4[3]
        has_deb = (d_pig + d_met + d_kry + d_spi) > 0
    elif isinstance(raw_rubble, Mapping):
        d_pig = max(0, coerce_int(raw_rubble.get("pig_iron", raw_rubble.get("pig", 0)), 0))
        d_met = max(0, coerce_int(raw_rubble.get("metal", 0), 0))
        d_kry = max(0, coerce_int(raw_rubble.get("kryptonite", 0), 0))
        d_spi = max(0, coerce_int(raw_rubble.get("spice", 0), 0))
        has_deb = (d_pig + d_met + d_kry + d_spi) > 0
    else:
        has_deb = bool(raw_rubble)
    return has_rubble_key, has_deb, d_pig, d_met, d_kry, d_spi


def parse_json_element_pos_meta(soup: BeautifulSoup) -> dict[int, JsonSlotMeta]:
    """Extract per-position colonization, temperature, and rubble metadata from ``#json-element``."""
    json_el = soup.find(id="json-element")
    if not isinstance(json_el, Tag):
        return {}
    raw_jtxt = (json_el.string if json_el.string is not None else json_el.get_text("", strip=True)) or ""
    if not raw_jtxt.strip():
        return {}
    try:
        parsed_j: object = json.loads(raw_jtxt)
    except (ValueError, TypeError):
        return {}
    planets_j = parsed_j.get("planets") if isinstance(parsed_j, Mapping) else None
    if isinstance(planets_j, Mapping):
        iter_items: list[tuple[object, object]] = list(planets_j.items())
    elif isinstance(planets_j, Sequence) and not isinstance(planets_j, (str, bytes)):
        iter_items = [(idx + 1, val) for idx, val in enumerate(planets_j)]
    else:
        return {}

    pos_meta: dict[int, JsonSlotMeta] = {}
    for raw_pk, pdata in iter_items:
        if not isinstance(pdata, Mapping):
            continue
        p_pos = coerce_int(pdata.get("pla", pdata.get("position", raw_pk)), 0)
        if not (1 <= p_pos <= 16):
            continue
        uid_v = coerce_int(pdata.get("uid", pdata.get("user_id", 0)), 0)
        pname_j = str(pdata.get("name") or pdata.get("planet_name") or "").strip()
        owner_j = str(pdata.get("uname") or pdata.get("owner") or "").strip()
        is_sp_j = "spacia" in pname_j.lower() or "spacia" in owner_j.lower() or bool(pdata.get("spacia"))
        is_free_j = (uid_v == 0) and not pname_j and not owner_j and not is_sp_j
        c_min: int | None = None
        c_max: int | None = None
        uf_exact: int | None = None
        temp_j: int | None = None
        raw_temp = pdata.get("temperature")
        if raw_temp is not None and not isinstance(raw_temp, bool):
            t_parsed = coerce_optional_int(raw_temp, None)
            if t_parsed is not None and -300 <= t_parsed <= 600:
                temp_j = t_parsed
        if is_free_j:
            col_arr = pdata.get("colonize")
            if isinstance(col_arr, Sequence) and not isinstance(col_arr, (str, bytes)) and len(col_arr) >= 2:
                v0, v1 = coerce_int(col_arr[0], 0), coerce_int(col_arr[1], 0)
                if v0 > 0 and v1 >= v0:
                    c_min, c_max = v0, v1
            elif isinstance(col_arr, Mapping):
                v0 = coerce_int(col_arr.get("minFields", col_arr.get("min_fields", col_arr.get("min", 0))), 0)
                v1 = coerce_int(col_arr.get("maxFields", col_arr.get("max_fields", col_arr.get("max", 0))), 0)
                if v0 > 0 and v1 >= v0:
                    c_min, c_max = v0, v1
                vf = col_arr.get("fields", col_arr.get("uninhabited_fields"))
                if isinstance(vf, int) and not isinstance(vf, bool) and 20 <= vf <= 1000:
                    uf_exact = vf
            elif isinstance(col_arr, int) and not isinstance(col_arr, bool) and 20 <= col_arr <= 1000:
                uf_exact = col_arr
            raw_exact = pdata.get("uninhabited_fields", pdata.get("fields"))
            if isinstance(raw_exact, int) and not isinstance(raw_exact, bool) and 20 <= raw_exact <= 1000:
                uf_exact = raw_exact
        has_rubble_key, has_deb, d_pig, d_met, d_kry, d_spi = _parse_json_rubble(pdata)
        pos_meta[p_pos] = JsonSlotMeta(
            c_min=c_min,
            c_max=c_max,
            uf_exact=uf_exact,
            temperature=temp_j,
            is_free=is_free_j,
            planet_name=pname_j,
            owner=owner_j,
            has_debris=has_deb,
            has_rubble_key=has_rubble_key,
            debris_pig_iron=d_pig,
            debris_metal=d_met,
            debris_kryptonite=d_kry,
            debris_spice=d_spi,
        )
    return pos_meta


def merge_json_pos_meta(
    slots: list[GalaxySlot],
    pos_meta: dict[int, JsonSlotMeta],
    gal: int,
    sys_num: int,
) -> list[GalaxySlot]:
    """Enrich parsed DOM slots (or synthesize slots when DOM cards are absent) from ``pos_meta``."""
    if not pos_meta:
        return slots
    if slots:
        enriched: list[GalaxySlot] = []
        for sl in slots:
            meta = pos_meta.get(sl.position)
            if meta is not None:
                sl = replace(
                    sl,
                    colonize_min_fields=meta.c_min
                    if (sl.status_color == "free" and meta.c_min is not None)
                    else sl.colonize_min_fields,
                    colonize_max_fields=meta.c_max
                    if (sl.status_color == "free" and meta.c_max is not None)
                    else sl.colonize_max_fields,
                    uninhabited_fields=meta.uf_exact
                    if (sl.status_color == "free" and meta.uf_exact is not None)
                    else sl.uninhabited_fields,
                    temperature=(
                        meta.temperature
                        if (meta.temperature is not None and (sl.status_color == "free" or sl.temperature is None))
                        else sl.temperature
                    ),
                    has_debris=meta.has_debris if meta.has_rubble_key else sl.has_debris,
                    debris_pig_iron=(meta.debris_pig_iron if meta.has_debris else 0)
                    if meta.has_rubble_key
                    else sl.debris_pig_iron,
                    debris_metal=(meta.debris_metal if meta.has_debris else 0)
                    if meta.has_rubble_key
                    else sl.debris_metal,
                    debris_kryptonite=(meta.debris_kryptonite if meta.has_debris else 0)
                    if meta.has_rubble_key
                    else sl.debris_kryptonite,
                    debris_spice=(meta.debris_spice if meta.has_debris else 0)
                    if meta.has_rubble_key
                    else sl.debris_spice,
                )
            enriched.append(sl)
        return enriched

    synthesized: list[GalaxySlot] = []
    for p_pos in sorted(pos_meta):
        meta = pos_meta[p_pos]
        is_sp_j = "spacia" in meta.planet_name.lower() or "spacia" in meta.owner.lower()
        synthesized.append(
            GalaxySlot(
                coords=f"{gal:02d}:{sys_num:03d}:{p_pos:02d}",
                position=p_pos,
                name=meta.planet_name,
                owner="Spacia" if (is_sp_j and not meta.owner) else meta.owner,
                status_color="free" if meta.is_free else ("yellow" if is_sp_j else "light_red"),
                has_debris=meta.has_debris,
                is_spacia=is_sp_j,
                debris_pig_iron=meta.debris_pig_iron if meta.has_debris else 0,
                debris_metal=meta.debris_metal if meta.has_debris else 0,
                debris_kryptonite=meta.debris_kryptonite if meta.has_debris else 0,
                debris_spice=meta.debris_spice if meta.has_debris else 0,
                colonize_min_fields=meta.c_min,
                colonize_max_fields=meta.c_max,
                uninhabited_fields=meta.uf_exact,
                temperature=meta.temperature,
            )
        )
    return synthesized
