"""Multi-year ships/week ramp projection from live empire floors to an optimized target plan."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import heapq
import math

from spaceinvasion.planner.config_optimizer._overrides_helpers import StateInput, json_ready_dict
from spaceinvasion.planner.config_optimizer._projection_eval import (
    _eval_snapshot,
    _init_research_levels,
    _init_sim_planets,
    _score_building_weight,
    _score_research_weight,
)
from spaceinvasion.planner.config_optimizer._projection_helpers import (
    _build_caches,
    _build_projection_config,
    _JobCandidate,
    _ProjectionConfig,
    _SimPlanet,
    _Snapshot,
)
from spaceinvasion.planner.config_optimizer._protocols import map_float
from spaceinvasion.planner.config_optimizer.tech_graph import TECH_GRAPH


class _ProjectionRunner:
    """Discrete-event build/research queue simulator for multi-year weekly ship ramp projections."""

    def __init__(
        self,
        cfg: _ProjectionConfig,
        sim_planets: list[_SimPlanet],
        cur_res: dict[str, int],
        tgt_res: dict[str, int],
        initial_stock_sp: float,
    ) -> None:
        self.cfg = cfg
        self.sim_planets = sim_planets
        self.cur_res = cur_res
        self.tgt_res = tgt_res
        self.caches = _build_caches(cfg, sim_planets)
        self.total_upgrade_levels = int(
            sum(max(0, int(sp.tgt[bk]) - int(sp.cur[bk])) for sp in sim_planets for bk in cfg.all_bkeys)
            + sum(max(0, int(tgt_res[rk]) - int(cur_res[rk])) for rk in tgt_res)
        )
        self.completed_upgrade_levels = 0
        self.t_now = 0.0
        self.stock_sp = initial_stock_sp
        self.planet_busy: list[bool] = [False] * len(sim_planets)
        self.research_busy = False
        self.in_flight: list[tuple[float, int, str, int, str, int]] = []
        self.ev_seq = 0
        self.safety_guard = 0
        self.snap: _Snapshot = _eval_snapshot(cfg, sim_planets, cur_res, self.caches)
        self.current_ships_wk = float(self.snap.ships_per_week)

    def _pick_best_planet_candidate(
        self,
        cur_snap: _Snapshot,
        best: _JobCandidate | None,
    ) -> _JobCandidate | None:
        cfg = self.cfg
        sp_s = cur_snap.sp_per_s
        for p_idx, sp in enumerate(self.sim_planets):
            if self.planet_busy[p_idx]:
                continue
            cb = sp.cur
            tb = sp.tgt
            fields_used = sum(cb.values())
            has_builds_left = any(int(tb[bk]) > int(cb[bk]) for bk in cfg.all_bkeys)
            if has_builds_left and fields_used >= sp.fields_max:
                dem_cands = [bk for bk in cfg.all_bkeys if int(cb[bk]) > int(tb[bk])]
                if dem_cands:
                    bk_d = dem_cands[0]
                    dur_d = self.caches.building_dur_s(sp, bk_d, int(cb[bk_d]))
                    cand_d = _JobCandidate(1e9, 0.0, dur_d, "demolish", p_idx, bk_d, int(cb[bk_d]) - 1)
                    if best is None or cand_d.score > best.score:
                        best = cand_d
                    continue
            for bk in cfg.all_bkeys:
                c_l = int(cb[bk])
                t_l = int(tb[bk])
                if c_l >= t_l:
                    continue
                if any(int(cb.get(rk_b, 0)) < int(rl_b) for rk_b, rl_b in cfg.b_prereqs_map[bk].items()):
                    continue
                if any(int(self.cur_res.get(rk_r, 0)) < int(rl_r) for rk_r, rl_r in cfg.b_r_prereqs_map[bk].items()):
                    continue
                n_l = c_l + 1
                cost = self.caches.building_cost_sp(bk, n_l)
                dur = self.caches.building_dur_s(sp, bk, n_l)
                eff_dt = max(60.0, dur + cost / max(1e-3, sp_s))
                weight = _score_building_weight(
                    cfg, sp, bk, c_l, t_l, n_l, cost, self.cur_res, self.tgt_res, cur_snap, self.caches
                )
                cand = _JobCandidate(weight / eff_dt, cost, dur, "build", p_idx, bk, n_l)
                if best is None or cand.score > best.score:
                    best = cand
        return best

    def _pick_best_research_candidate(
        self,
        cur_snap: _Snapshot,
        best: _JobCandidate | None,
    ) -> _JobCandidate | None:
        if self.research_busy or not any(int(sp.cur.get("b8", 0)) > 0 for sp in self.sim_planets):
            return best
        best_lab_b8 = max((int(sp.cur.get("b8", 0)) for sp in self.sim_planets), default=0)
        sp_s = cur_snap.sp_per_s
        for rk, t_l in self.tgt_res.items():
            c_l = int(self.cur_res.get(rk, 0))
            if c_l >= t_l:
                continue
            prereqs_r = TECH_GRAPH.prereqs_of.get(rk, ())
            req_b8 = max([1, *(int(pr_l) for pr_k, pr_l in prereqs_r if pr_k == "b8")])
            if best_lab_b8 < req_b8:
                continue
            if any(int(self.cur_res.get(pr_k, 0)) < int(pr_l) for pr_k, pr_l in prereqs_r if pr_k.startswith("r")):
                continue
            n_l = c_l + 1
            cost = self.caches.research_cost_sp(rk, n_l)
            dur = self.caches.research_dur_s(self.sim_planets, rk, n_l)
            eff_dt = max(60.0, dur + cost / max(1e-3, sp_s))
            weight = _score_research_weight(self.cfg, rk, c_l, cost, sp_s)
            cand = _JobCandidate(weight / eff_dt, cost, dur, "research", -1, rk, n_l)
            if best is None or cand.score > best.score:
                best = cand
        return best

    def _pick_best_candidate(self) -> _JobCandidate | None:
        best = self._pick_best_planet_candidate(self.snap, None)
        return self._pick_best_research_candidate(self.snap, best)

    def _enqueue_job(self, cand: _JobCandidate) -> None:
        if cand.kind == "research":
            self.research_busy = True
        else:
            self.planet_busy[cand.p_idx] = True
        self.ev_seq += 1
        heapq.heappush(
            self.in_flight, (self.t_now + cand.dur_s, self.ev_seq, cand.kind, cand.p_idx, cand.key, cand.next_lvl)
        )

    def _pop_until(self, target_t: float) -> None:
        while self.in_flight and self.in_flight[0][0] <= target_t + 1e-6:
            fin_s, _seq, kind, p_idx, key, nxt_lvl = heapq.heappop(self.in_flight)
            if fin_s > self.t_now:
                self.stock_sp += (fin_s - self.t_now) * self.snap.sp_per_s
                self.t_now = fin_s
            if kind == "research":
                self.cur_res[key] = max(int(self.cur_res.get(key, 0)), int(nxt_lvl))
                self.research_busy = False
                self.completed_upgrade_levels += 1
            elif kind == "demolish":
                self.sim_planets[p_idx].cur[key] = int(nxt_lvl)
                self.planet_busy[p_idx] = False
            else:
                self.sim_planets[p_idx].cur[key] = int(nxt_lvl)
                self.planet_busy[p_idx] = False
                self.completed_upgrade_levels += 1
            self.snap = _eval_snapshot(self.cfg, self.sim_planets, self.cur_res, self.caches)

    def _advance_to_sample(self, sample_t: float) -> None:
        while self.t_now < sample_t - 1e-6 and self.safety_guard < 50_000:
            self.safety_guard += 1
            cand = self._pick_best_candidate()
            if cand is not None and self.stock_sp >= cand.cost_sp * (1.0 - 1e-9) - 1.0:
                self.stock_sp = max(0.0, self.stock_sp - cand.cost_sp)
                self._enqueue_job(cand)
                continue

            next_fin = self.in_flight[0][0] if self.in_flight else math.inf
            if cand is not None:
                dt_afford = max(0.0, (cand.cost_sp - self.stock_sp) / max(1e-4, self.snap.sp_per_s))
                t_afford = self.t_now + dt_afford
            else:
                t_afford = math.inf

            next_event_t = min(sample_t, next_fin, t_afford)
            if not math.isfinite(next_event_t):
                break
            if self.in_flight and next_fin <= next_event_t + 1e-6 and next_fin <= sample_t:
                self._pop_until(next_fin)
            elif cand is not None and t_afford <= sample_t and t_afford <= next_fin:
                self.t_now = max(self.t_now, t_afford)
                self.stock_sp = 0.0
                self._enqueue_job(cand)
            else:
                self.stock_sp += max(0.0, sample_t - self.t_now) * self.snap.sp_per_s
                self.t_now = sample_t
                break

    def _kickoff_week_zero(self) -> None:
        while self.safety_guard < 50_000:
            cand0 = self._pick_best_candidate()
            if cand0 is None or self.stock_sp + 1e-6 < cand0.cost_sp:
                break
            self.safety_guard += 1
            self.stock_sp = max(0.0, self.stock_sp - cand0.cost_sp)
            self._enqueue_job(cand0)

    def run(self) -> tuple[list[dict[str, object]], int | None, int | None]:
        """Run the weekly discrete-event simulation across the configured horizon."""
        weeks_out: list[dict[str, object]] = []
        weeks_to_90: int | None = None
        weeks_to_full: int | None = None
        target_ships_wk = self.cfg.target_ships_wk

        for wk in range(0, self.cfg.total_weeks + 1):
            sample_t = float(wk) * self.cfg.seconds_per_week
            self._advance_to_sample(sample_t)
            self._pop_until(sample_t)
            if wk == 0:
                self._kickoff_week_zero()

            ships_wk = self.snap.ships_per_week
            if target_ships_wk > 0.0:
                if self.completed_upgrade_levels >= self.total_upgrade_levels:
                    ships_wk = min(ships_wk, target_ships_wk)
                if self.completed_upgrade_levels >= self.total_upgrade_levels and not self.in_flight:
                    ships_wk = target_ships_wk
            comp_pct = (
                100.0
                if self.total_upgrade_levels <= 0
                else round(
                    min(100.0, 100.0 * float(self.completed_upgrade_levels) / float(self.total_upgrade_levels)), 1
                )
            )
            if weeks_to_90 is None and target_ships_wk > 0.0 and ships_wk >= 0.90 * target_ships_wk:
                weeks_to_90 = wk
            if weeks_to_full is None and (
                self.completed_upgrade_levels >= self.total_upgrade_levels
                or (target_ships_wk > 0.0 and ships_wk >= 0.999 * target_ships_wk)
            ):
                weeks_to_full = wk

            weeks_out.append(
                {
                    "week": wk,
                    "year": 1 if wk == 0 else ((wk - 1) // 52) + 1,
                    "week_in_year": 0 if wk == 0 else ((wk - 1) % 52) + 1,
                    "ships_per_week": round(ships_wk, 2),
                    "resource_cap_per_week": round(self.snap.resource_cap_per_week, 2),
                    "shipyard_cap_per_week": round(self.snap.shipyard_cap_per_week, 2),
                    "bottleneck": self.snap.bottleneck,
                    "completion_pct": comp_pct,
                }
            )
        return weeks_out, weeks_to_90, weeks_to_full


def _aggregate_years(weeks_out: Sequence[Mapping[str, object]], years_n: int) -> list[dict[str, object]]:
    """Aggregate weekly projection samples into per-year rate and cumulative ship summaries."""
    years_out: list[dict[str, object]] = []
    cumulative_ships = 0.0
    for yr in range(1, years_n + 1):
        w_start = (yr - 1) * 52
        w_end = yr * 52
        slice_wks = weeks_out[w_start + 1 : w_end + 1]
        start_rate = map_float(weeks_out[w_start], "ships_per_week", 0.0)
        end_rate = map_float(slice_wks[-1], "ships_per_week", 0.0) if slice_wks else start_rate
        avg_rate = (
            sum(map_float(w, "ships_per_week", 0.0) for w in slice_wks) / len(slice_wks) if slice_wks else start_rate
        )
        peak_rate = max((map_float(w, "ships_per_week", 0.0) for w in slice_wks), default=start_rate)
        yr_ships = sum(map_float(w, "ships_per_week", 0.0) for w in slice_wks)
        cumulative_ships += yr_ships
        end_row = slice_wks[-1] if slice_wks else weeks_out[w_start]
        years_out.append(
            {
                "year": yr,
                "label": f"Year {yr}",
                "start_ships_per_week": round(start_rate, 2),
                "avg_ships_per_week": round(avg_rate, 2),
                "end_ships_per_week": round(end_rate, 2),
                "peak_ships_per_week": round(peak_rate, 2),
                "delta_ships_per_week": round(end_rate - start_rate, 2),
                "total_ships_produced": round(yr_ships, 1),
                "cumulative_ships_produced": round(cumulative_ships, 1),
                "bottleneck": str(end_row["bottleneck"]),
                "completion_pct": map_float(end_row, "completion_pct", 0.0),
            }
        )
    return years_out


def compute_yearly_ships_per_week_projection(
    result: Mapping[str, object],
    state: StateInput | None = None,
    *,
    horizon_years: int = 5,
) -> dict[str, object]:
    """Project year-by-year ``ships / week`` as the empire builds from current floors to ``result``."""
    cfg = _build_projection_config(result, state, horizon_years)
    cur_res, tgt_res = _init_research_levels(result, state)
    sim_planets, initial_stock_sp = _init_sim_planets(result, state, cfg.all_bkeys)
    runner = _ProjectionRunner(cfg, sim_planets, cur_res, tgt_res, initial_stock_sp)
    weeks_out, weeks_to_90, weeks_to_full = runner.run()
    years_out = _aggregate_years(weeks_out, cfg.years_n)
    return json_ready_dict(
        {
            "target_ship_key": cfg.ship_key,
            "target_ship": cfg.ship_name,
            "horizon_years": cfg.years_n,
            "total_weeks": cfg.total_weeks,
            "current_ships_per_week": round(runner.current_ships_wk, 2),
            "target_ships_per_week": round(cfg.target_ships_wk, 2),
            "target_ships_per_month": round(cfg.summary_ships_mo, 2),
            "total_upgrade_levels": runner.total_upgrade_levels,
            "weeks_to_90_pct": weeks_to_90,
            "weeks_to_full_target": weeks_to_full,
            "years_to_full_target": round(weeks_to_full / 52.0, 2) if weeks_to_full is not None else None,
            "years": years_out,
            "weeks": weeks_out,
        }
    )


__all__ = ["compute_yearly_ships_per_week_projection"]
