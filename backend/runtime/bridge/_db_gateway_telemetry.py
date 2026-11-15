"""Transport dispatch telemetry ring and universe research-max aggregation mixin."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
import time

from backend.core import catalog as game_data
from backend.core.coords import normalize_coords
from backend.core.model import Resources
from backend.db.dto.serializers import research_max_to_dict
from backend.db.queries import (
    query_executed_transport_actions,
    query_latest_own_researches_and_nick,
    query_spy_research_observations,
)
from backend.db.repos import UniverseResearchMaxInput
from backend.runtime.bridge._db_gateway_common import (
    _clean_str,
    _GatewayCore,
    _loads_dict,
    _safe_float,
    _safe_int,
)

_ANY_GSP_RE = re.compile(r"\b\d{1,2}:\d{1,3}:\d{1,2}\b")
_CARGO_SHORT_RE = re.compile(r"\b([PMKS])\s*[:=]\s*([0-9][0-9.,']*)\b", re.IGNORECASE)
_CARGO_SUFFIX_RE = re.compile(r"\b([0-9][0-9.,']*)\s*([PMKS])\b", re.IGNORECASE)
_CARGO_LONG_RE = re.compile(
    r"(?:(pig[\s_]+iron|metal|kryptonite|spice)\s*[:=]\s*([0-9][0-9.,']*)"
    r"|([0-9][0-9.,']*)\s*(pig[\s_]+iron|metal|kryptonite|spice))",
    re.IGNORECASE,
)
_SHORT_TO_RES: dict[str, str] = {"p": "pig_iron", "m": "metal", "k": "kryptonite", "s": "spice"}
_RECENT_TRANSPORT_RING_MAX = 120


def _normalize_cargo_dict(cargo: object) -> dict[str, int]:
    if isinstance(cargo, Resources):
        return {
            "pig_iron": game_data.cap_json_int(max(0, _safe_int(cargo.pig_iron, 0))),
            "metal": game_data.cap_json_int(max(0, _safe_int(cargo.metal, 0))),
            "kryptonite": game_data.cap_json_int(max(0, _safe_int(cargo.kryptonite, 0))),
            "spice": game_data.cap_json_int(max(0, _safe_int(cargo.spice, 0))),
        }
    src = cargo if isinstance(cargo, Mapping) else {}
    return {
        rk: game_data.cap_json_int(max(0, _safe_int(src.get(rk), 0)))
        for rk in ("pig_iron", "metal", "kryptonite", "spice")
    }


def _normalize_transport_entry(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, Mapping):
        return None
    orig = normalize_coords(_clean_str(raw.get("origin_coords")).strip())
    dest = normalize_coords(_clean_str(raw.get("dest_coords")).strip())
    if not orig or not dest:
        return None
    cargo = _normalize_cargo_dict(raw.get("cargo"))
    if sum(cargo.values()) <= 0:
        return None
    ships_raw = raw.get("ships")
    if isinstance(ships_raw, Mapping):
        ships_str = ", ".join(f"{k}:{_safe_int(v, 0)}" for k, v in ships_raw.items() if _safe_int(v, 0) > 0)
    else:
        ships_str = _clean_str(ships_raw, 120).strip()
    return {
        "origin_coords": orig,
        "dest_coords": dest,
        "cargo": cargo,
        "ships": ships_str,
        "ts": round(_safe_float(raw.get("ts"), 0.0), 3),
        "source": _clean_str(raw.get("source") or "dispatch", 32).strip().lower() or "dispatch",
    }


def _parse_transport_cargo_from_text(text: str) -> dict[str, int] | None:
    if not text:
        return None
    totals = {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    found = False
    for pattern, key_grp, val_grp in ((_CARGO_SHORT_RE, 1, 2), (_CARGO_SUFFIX_RE, 2, 1)):
        for match in pattern.finditer(text):
            digits = re.sub(r"[^\d]", "", match.group(val_grp), flags=re.ASCII)
            if not digits:
                continue
            val = game_data.cap_json_int(int(digits))
            key = _SHORT_TO_RES[match.group(key_grp).lower()]
            if val > 0 and totals[key] == 0:
                totals[key] = val
                found = True
        if found:
            return totals
    for match in _CARGO_LONG_RE.finditer(text):
        lbl = (match.group(1) or match.group(4) or "").lower().replace(" ", "_")
        digits = re.sub(r"[^\d]", "", match.group(2) or match.group(3) or "", flags=re.ASCII)
        if not digits or lbl not in totals:
            continue
        val = game_data.cap_json_int(int(digits))
        if val > 0 and totals[lbl] == 0:
            totals[lbl] = val
            found = True
    return totals if found else None


def _catalog_research_defs() -> list[tuple[int, str, str, str, int | None]]:
    defs: list[tuple[int, str, str, str, int | None]] = []
    for slug, info in game_data.RESEARCHES.items():
        if not isinstance(info, Mapping):
            continue
        raw_id = str(info.get("id") or "").strip()
        if raw_id.startswith("r") and raw_id[1:].isascii() and raw_id[1:].isdigit():
            rid, rkey = int(raw_id[1:]), raw_id
        else:
            rid, rkey = 16, str(slug).strip()
        name = str(info.get("name") or slug).strip()
        lvls = info.get("levels")
        cat_max: int | None = None
        if isinstance(lvls, Mapping) and lvls:
            int_lvls = [int(k) for k in lvls if isinstance(k, int) or (isinstance(k, str) and k.isdigit())]
            if int_lvls:
                cat_max = max(int_lvls)
        defs.append((rid, rkey, str(slug), name, cat_max))
    defs.sort(key=lambda item: (item[0], item[2]))
    return defs


def _merge_server_research_records(
    raw_meta: object,
    server_records: Mapping[str, object] | None,
    slug_to_key: Mapping[str, str],
    name_to_key: Mapping[str, str],
) -> tuple[dict[str, dict[str, object]], bool]:
    merged: dict[str, dict[str, object]] = {}
    if isinstance(raw_meta, Mapping):
        for rk, rv in raw_meta.items():
            if isinstance(rv, Mapping):
                merged[str(rk)] = {
                    "level": max(0, _safe_int(rv.get("level"), 0)),
                    "holder": _clean_str(rv.get("holder")),
                    "coords": normalize_coords(_clean_str(rv.get("coords"))) if rv.get("coords") else "",
                }
    changed = False
    if isinstance(server_records, Mapping):
        for raw_k, raw_v in server_records.items():
            ks = _clean_str(raw_k).strip()
            canon_k = slug_to_key.get(ks.lower(), name_to_key.get(ks.lower(), ks))
            if isinstance(raw_v, Mapping):
                lvl_v = max(0, _safe_int(raw_v.get("level"), 0))
                h_v = _clean_str(raw_v.get("holder"))
                c_v = normalize_coords(_clean_str(raw_v.get("coords")).strip()) if raw_v.get("coords") else ""
            else:
                lvl_v, h_v, c_v = max(0, _safe_int(raw_v, 0)), "", ""
            if lvl_v <= 0:
                continue
            prev_s = merged.get(canon_k)
            prev_lvl = _safe_int(prev_s.get("level"), 0) if prev_s is not None else 0
            if prev_s is None or lvl_v > prev_lvl or (lvl_v == prev_lvl and h_v and not prev_s.get("holder")):
                merged[canon_k] = {"level": lvl_v, "holder": h_v, "coords": c_v}
                changed = True
    return merged, changed


def _collect_spied_research_best(
    observations: Sequence[tuple[str, str, str]],
    slug_to_key: Mapping[str, str],
    name_to_key: Mapping[str, str],
) -> dict[str, tuple[int, str, str]]:
    best: dict[str, tuple[int, str, str]] = {}
    for coords_s, owner_s, rjson in observations:
        for rk_raw, rv_raw in _loads_dict(rjson).items():
            canon_rk = slug_to_key.get(rk_raw.lower(), name_to_key.get(rk_raw.lower(), rk_raw))
            rlvl = max(0, _safe_int(rv_raw, 0))
            if rlvl <= 0:
                continue
            cur_b = best.get(canon_rk)
            if cur_b is None or rlvl >= cur_b[0]:
                best[canon_rk] = (
                    rlvl,
                    owner_s or (cur_b[1] if (cur_b and cur_b[0] == rlvl) else ""),
                    coords_s or (cur_b[2] if (cur_b and cur_b[0] == rlvl) else ""),
                )
    return best


class _TelemetryGatewayMixin(_GatewayCore):
    """Recent inter-planet transport telemetry and universe research maximum tracking."""

    def get_recent_transport_dispatches(
        self,
        *,
        max_age_seconds: float | None = None,
        limit: int = _RECENT_TRANSPORT_RING_MAX,
        now_ts: float | None = None,
    ) -> list[dict[str, object]]:
        """Return bounded ring of recent inter-planet transport dispatches from `meta`."""
        raw = self.load_meta_json("recent_transport_dispatches")
        items: list[dict[str, object]] = []
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for row in raw:
                if (norm := _normalize_transport_entry(row)) is not None:
                    items.append(norm)
        if max_age_seconds is not None and max_age_seconds > 0.0:
            ref_now = _safe_float(now_ts, time.time()) if now_ts is not None else time.time()
            cutoff = ref_now - float(max_age_seconds)
            items = [it for it in items if _safe_float(it["ts"], 0.0) >= cutoff]
        lim = max(1, min(500, _safe_int(limit, _RECENT_TRANSPORT_RING_MAX)))
        return items[-lim:]

    def record_transport_dispatch(
        self,
        *,
        origin_coords: str,
        dest_coords: str,
        cargo: object,
        ships: object = "",
        source: str = "dispatch",
        ts: float | None = None,
        max_entries: int = _RECENT_TRANSPORT_RING_MAX,
    ) -> list[dict[str, object]]:
        """Append a live or observed outbound transport dispatch to the bounded `meta` ring."""
        now_ts = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        entry = _normalize_transport_entry(
            {
                "origin_coords": origin_coords,
                "dest_coords": dest_coords,
                "cargo": _normalize_cargo_dict(cargo),
                "ships": ships,
                "ts": now_ts,
                "source": source,
            }
        )
        lim = max(1, min(500, _safe_int(max_entries, _RECENT_TRANSPORT_RING_MAX)))
        items = self.get_recent_transport_dispatches(limit=500)
        if entry is None:
            return items[-lim:]
        dedupe_window = 7200.0 if entry["source"] == "observed_outbound" else 5.0
        for prev in reversed(items):
            if (now_ts - _safe_float(prev["ts"], 0.0)) > dedupe_window:
                break
            if (
                prev["origin_coords"] == entry["origin_coords"]
                and prev["dest_coords"] == entry["dest_coords"]
                and prev["cargo"] == entry["cargo"]
                and (entry["source"] == "observed_outbound" or prev["source"] == entry["source"])
            ):
                if entry["ships"] and not prev["ships"]:
                    prev["ships"] = entry["ships"]
                    self.save_meta_json("recent_transport_dispatches", items[-lim:], ts=now_ts)
                return items[-lim:]
        items.append(entry)
        trimmed = items[-lim:]
        self.save_meta_json("recent_transport_dispatches", trimmed, ts=now_ts)
        return trimmed

    def get_recent_transport_actions_fallback(
        self,
        *,
        max_age_seconds: float = 86400.0,
        limit: int = 60,
        now_ts: float | None = None,
    ) -> list[dict[str, object]]:
        """Read recent executed live `TRANSPORT` actions as fallback cargo telemetry."""
        ref_now = _safe_float(now_ts, time.time()) if now_ts is not None else time.time()
        cutoff = ref_now - max(60.0, float(max_age_seconds))
        with self._store.session() as session:
            rows = query_executed_transport_actions(session, since_ts=cutoff, limit=limit)
        out: list[dict[str, object]] = []
        for r in rows:
            orig = normalize_coords(r.planet_coords or "")
            m_dest = _ANY_GSP_RE.search(f"{r.target_name or ''} {r.message} {r.reason}")
            dest = normalize_coords(m_dest.group(0)) if m_dest else ""
            if not orig or not dest:
                continue
            cargo = _parse_transport_cargo_from_text(f"{r.message} {r.reason}")
            if cargo and sum(cargo.values()) > 0:
                out.append(
                    {
                        "origin_coords": orig,
                        "dest_coords": dest,
                        "cargo": cargo,
                        "ships": "",
                        "ts": round(r.ts, 3),
                        "source": "action_history",
                    }
                )
        return out

    def refresh_universe_research_max(
        self,
        *,
        own_researches: Mapping[str, object] | None = None,
        own_nick: str = "",
        server_records: Mapping[str, object] | None = None,
        source: str = "refresh",
        ts: float | None = None,
    ) -> list[dict[str, object]]:
        """Recompute and persist `universe_research_max` across server records, spy reports, and own techs."""
        eff_now = round(_safe_float(ts, time.time()) if ts is not None else time.time(), 3)
        defs = _catalog_research_defs()
        slug_to_key = {slug: rkey for _, rkey, slug, _, _ in defs}
        name_to_key = {name.lower(): rkey for _, rkey, _, name, _ in defs}
        merged_srv, srv_changed = _merge_server_research_records(
            self.load_meta_json("universe_research_records"), server_records, slug_to_key, name_to_key
        )
        if srv_changed:
            self.save_meta_json("universe_research_records", merged_srv, ts=eff_now)

        eff_own: dict[str, int] = {}
        eff_nick = _clean_str(own_nick)
        with self._store.repositories() as repos:
            if own_researches is None:
                res_json, db_nick = query_latest_own_researches_and_nick(repos.session)
                eff_own = {ok: max(0, _safe_int(ov, 0)) for ok, ov in _loads_dict(res_json).items()}
                eff_nick = eff_nick or db_nick
            else:
                eff_own = {str(ok).strip(): max(0, _safe_int(ov, 0)) for ok, ov in own_researches.items()}

            prev_by_key = {r.research_key: r for r in repos.telemetry.list_universe_research_max()}
            spied_best = _collect_spied_research_best(
                query_spy_research_observations(repos.session), slug_to_key, name_to_key
            )
            entries: list[UniverseResearchMaxInput] = []
            for rid, rkey, slug, name, cat_max in defs:
                prev_r = prev_by_key.get(rkey)
                own_lvl = max(0, eff_own.get(rkey, eff_own.get(slug, 0)))
                obs_spied, obs_holder, obs_coords = spied_best.get(rkey, (0, "", ""))
                spied_lvl = max(prev_r.spied_max_level if prev_r else 0, obs_spied)
                prev_srv = prev_r.server_record_level if prev_r else None
                srv_entry = merged_srv.get(rkey)
                srv_cand = max(0, _safe_int(srv_entry.get("level"), 0)) if srv_entry else 0
                srv_lvl = (
                    max(prev_srv, srv_cand)
                    if (prev_srv is not None and srv_cand > 0)
                    else (srv_cand if srv_cand > 0 else prev_srv)
                )
                prev_univ = prev_r.universe_max_level if prev_r else 0
                univ_max = max(prev_univ, own_lvl, spied_lvl, srv_lvl or 0)
                holder = holder_coords = ""
                if univ_max > 0:
                    if srv_lvl == univ_max and srv_entry and srv_entry.get("holder"):
                        holder, holder_coords = str(srv_entry["holder"]), str(srv_entry.get("coords") or "")
                    elif obs_spied == univ_max and obs_spied > 0:
                        holder, holder_coords = obs_holder or obs_coords, obs_coords
                    elif prev_r is not None and prev_univ == univ_max and prev_r.holder.strip():
                        holder, holder_coords = prev_r.holder, prev_r.holder_coords
                    elif own_lvl == univ_max and own_lvl > 0:
                        holder = eff_nick or "You"
                entries.append(
                    UniverseResearchMaxInput(
                        rkey,
                        rid,
                        slug,
                        name,
                        own_lvl,
                        spied_lvl,
                        srv_lvl,
                        univ_max,
                        cat_max,
                        holder[:80],
                        holder_coords[:24],
                    )
                )
            dtos = repos.telemetry.bulk_upsert_universe_research_max(
                entries, source=source, append_history=(source != "auto_seed"), ts=eff_now
            )
        self._bump_change()
        return [research_max_to_dict(d) for d in dtos]

    def get_universe_research_max(
        self, own_researches: Mapping[str, object] | None = None, own_nick: str = ""
    ) -> list[dict[str, object]]:
        """Read persisted `universe_research_max` rows, auto-seeding or syncing own levels as needed."""
        defs = _catalog_research_defs()
        with self._store.repositories() as repos:
            rows = repos.telemetry.list_universe_research_max()
        if len(rows) < len(defs):
            return self.refresh_universe_research_max(
                own_researches=own_researches, own_nick=own_nick, source="auto_seed"
            )
        if isinstance(own_researches, Mapping) and own_researches:
            own_lookup = {str(k).strip(): max(0, _safe_int(v, 0)) for k, v in own_researches.items()}
            if any(own_lookup.get(r.research_key, own_lookup.get(r.slug, r.own_level)) != r.own_level for r in rows):
                return self.refresh_universe_research_max(
                    own_researches=own_researches, own_nick=own_nick, source="auto_seed"
                )
        return [research_max_to_dict(r) for r in rows]


__all__ = ["_TelemetryGatewayMixin"]
