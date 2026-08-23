"""Typed game state models and pure HTML/JSON parsers for SpaceInvasion Classic."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from spaceinvasion import game_data

BUILDING_ID_BY_NAME: dict[str, int] = {
    "pig iron mine": 1,
    "blast furnace": 2,
    "kryptonite mine": 3,
    "spice mine": 4,
    "fusion plant": 5,
    "thermosolar plant": 6,
    "development center": 7,
    "research lab": 8,
    "weapons factory": 9,
    "fleet base": 10,
    "pig iron warehouse": 11,
    "metal warehouse": 12,
    "kryptonite warehouse": 13,
    "spice warehouse": 14,
    "particle cannon": 15,
    "particle shield": 16,
    "microsystem accelerator": 17,
    "teleporter": 18,
    "galaxy scanner": 19,
    "tunnel system": 20,
    "quantum array": 21,
}

_INT_RE = re.compile(r"[\d.]+")


def parse_si_int(raw: str) -> int:
    """Parse SpaceInvasion dot-grouped integer strings like `'2.000'` -> `2000`."""
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else 0


@dataclass(frozen=True)
class Resources:
    """Four primary SpaceInvasion Classic resources."""

    pig_iron: int = 0
    metal: int = 0
    kryptonite: int = 0
    spice: int = 0

    @classmethod
    def from_list(cls, values: list[int] | tuple[int, ...]) -> Resources:
        padded = list(values[:4]) + [0] * max(0, 4 - len(values))
        return cls(
            pig_iron=int(padded[0]),
            metal=int(padded[1]),
            kryptonite=int(padded[2]),
            spice=int(padded[3]),
        )

    def can_afford(self, cost: Resources) -> bool:
        return (
            self.pig_iron >= cost.pig_iron
            and self.metal >= cost.metal
            and self.kryptonite >= cost.kryptonite
            and self.spice >= cost.spice
        )

    def missing(self, cost: Resources) -> Resources:
        return Resources(
            pig_iron=max(0, cost.pig_iron - self.pig_iron),
            metal=max(0, cost.metal - self.metal),
            kryptonite=max(0, cost.kryptonite - self.kryptonite),
            spice=max(0, cost.spice - self.spice),
        )

    def is_zero(self) -> bool:
        return (
            self.pig_iron == 0
            and self.metal == 0
            and self.kryptonite == 0
            and self.spice == 0
        )

    def format_short(self) -> str:
        parts = []
        if self.pig_iron:
            parts.append(f"PigIron={self.pig_iron:,}")
        if self.metal:
            parts.append(f"Metal={self.metal:,}")
        if self.kryptonite:
            parts.append(f"Krypt={self.kryptonite:,}")
        if self.spice:
            parts.append(f"Spice={self.spice:,}")
        return ", ".join(parts) if parts else "0"


@dataclass(frozen=True)
class EnergyBalance:
    """Instantaneous planetary energy production and demand."""

    produced: int = 0
    used: int = 0

    @property
    def net(self) -> int:
        return self.produced - self.used


@dataclass(frozen=True)
class BuildingOffer:
    """Parsed building card from `internalBuildings`."""

    building_id: int
    name: str
    current_level: int
    target_level: int
    cost: Resources
    can_build: bool
    status_text: str
    build_href: Optional[str] = None


@dataclass(frozen=True)
class ResearchOffer:
    """Parsed research card from `internalResearch`."""

    research_id: int
    research_key: str
    name: str
    current_level: int
    target_level: int
    cost: Resources
    can_research: bool
    status_text: str
    research_href: Optional[str] = None
    active: bool = False


@dataclass(frozen=True)
class UnitOffer:
    """Parsed ship or defense card from `internalWeaponFactory` or `internalDefense`."""

    unit_type: str  # "ship" | "defense"
    unit_id: int
    name: str
    owned: int
    cost: Resources
    can_build: bool
    input_name: Optional[str] = None
    status_text: str = ""


@dataclass(frozen=True)
class TutorialTask:
    """One requirement line inside the active tutorial step."""

    description: str
    building_name: Optional[str] = None
    building_id: Optional[int] = None
    current: int = 0
    target: int = 0
    category: str = "building"
    research_id: Optional[str] = None

    @property
    def completed(self) -> bool:
        return self.target > 0 and self.current >= self.target


@dataclass(frozen=True)
class TutorialStatus:
    """Active SpaceInvasion tutorial progress and tasks."""

    step: int
    total_steps: int
    tasks: tuple[TutorialTask, ...] = field(default_factory=tuple)
    reward_text: str = ""
    can_complete: bool = False


@dataclass(frozen=True)
class QueueJob:
    """Active construction, research, or shipyard job."""

    category: str
    name: str
    building_id: Optional[int] = None
    target_level: Optional[int] = None
    remaining_text: str = ""


@dataclass(frozen=True)
class FleetAlert:
    """Active fleet movement observed on `internalHome`."""

    mission: str
    hostile: bool
    summary: str


@dataclass(frozen=True)
class PlanetState:
    """Full state of a single planet or asteroid."""

    planet_id: int
    name: str
    coords: str
    planet_type: str
    temperature: int
    fields_used: int
    fields_max: int
    resources: Resources
    storage: Resources
    energy: EnergyBalance
    buildings: dict[str, int] = field(default_factory=dict)
    building_names: dict[str, str] = field(default_factory=dict)
    ships: dict[str, int] = field(default_factory=dict)
    defense: dict[str, int] = field(default_factory=dict)
    building_offers: tuple[BuildingOffer, ...] = field(default_factory=tuple)
    ship_offers: tuple[UnitOffer, ...] = field(default_factory=tuple)
    defense_offers: tuple[UnitOffer, ...] = field(default_factory=tuple)
    economy_rates: dict[str, int] = field(default_factory=dict)

    @property
    def fields_remaining(self) -> int:
        return max(0, self.fields_max - self.fields_used) if self.fields_max > 0 else 0

    def building_level(self, building_id: int) -> int:
        return int(self.buildings.get(f"b{building_id}", 0))

    def storage_fill_ratios(self) -> dict[str, float]:
        return {
            "pig_iron": (self.resources.pig_iron / self.storage.pig_iron)
            if self.storage.pig_iron > 0
            else 0.0,
            "metal": (self.resources.metal / self.storage.metal)
            if self.storage.metal > 0
            else 0.0,
            "kryptonite": (self.resources.kryptonite / self.storage.kryptonite)
            if self.storage.kryptonite > 0
            else 0.0,
            "spice": (self.resources.spice / self.storage.spice)
            if self.storage.spice > 0
            else 0.0,
        }


@dataclass(frozen=True)
class GameState:
    """Complete snapshot of the player's empire in the current universe."""

    universe_id: int
    user_id: int
    nickname: str
    timestamp: float
    planets: tuple[PlanetState, ...]
    active_planet_id: Optional[int] = None
    researches: dict[str, int] = field(default_factory=dict)
    research_names: dict[str, str] = field(default_factory=dict)
    specs: dict[str, int] = field(default_factory=dict)
    unread_messages: dict[str, int] = field(default_factory=dict)
    tutorial: Optional[TutorialStatus] = None
    active_queues: tuple[QueueJob, ...] = field(default_factory=tuple)
    fleet_alerts: tuple[FleetAlert, ...] = field(default_factory=tuple)
    research_offers: tuple[ResearchOffer, ...] = field(default_factory=tuple)
    speedup_factor: Optional[float] = None
    newbie_protection: bool = False
    account_points: Optional[int] = None
    account_rank: Optional[int] = None

    def active_planet(self) -> Optional[PlanetState]:
        if not self.planets:
            return None
        if self.active_planet_id is not None:
            for planet in self.planets:
                if planet.planet_id == self.active_planet_id:
                    return planet
        return self.planets[0]


def _extract_levels_and_names(mapping: Any) -> tuple[dict[str, int], dict[str, str]]:
    levels: dict[str, int] = {}
    names: dict[str, str] = {}
    if not isinstance(mapping, dict):
        return levels, names
    for key, val in mapping.items():
        if isinstance(val, dict):
            levels[str(key)] = int(val.get("level", 0) or 0)
            if val.get("name"):
                names[str(key)] = str(val["name"])
    return levels, names


def parse_planets_overview_json(json_text: str) -> GameState:
    """Parse `#json-container` from `internalPlanetsOverview` into a `GameState`."""
    payload = json.loads(json_text)
    info = payload.get("info", {})
    user = info.get("user", {})
    uni = info.get("universe", {})

    planets: list[PlanetState] = []
    for raw_p in payload.get("planets", []):
        b_levels, b_names = _extract_levels_and_names(raw_p.get("buildings"))
        s_levels, _ = _extract_levels_and_names(raw_p.get("ships"))
        d_levels, _ = _extract_levels_and_names(raw_p.get("defense"))
        energy_raw = raw_p.get("energy", {})
        fields_max = int(raw_p.get("fields", 0) or 0)
        fields_used = sum(b_levels.values())

        planets.append(
            PlanetState(
                planet_id=int(raw_p["id"]),
                name=str(raw_p.get("name", "")),
                coords=str(raw_p.get("coords", "")),
                planet_type=str(raw_p.get("type", "normal")),
                temperature=int(raw_p.get("temperature", 0) or 0),
                fields_used=fields_used,
                fields_max=fields_max,
                resources=Resources.from_list(raw_p.get("resources", [])),
                storage=Resources.from_list(raw_p.get("storage", [])),
                energy=EnergyBalance(
                    produced=int(energy_raw.get("produced", 0) or 0),
                    used=int(energy_raw.get("used", 0) or 0),
                ),
                buildings=b_levels,
                building_names=b_names,
                ships=s_levels,
                defense=d_levels,
            )
        )

    r_levels, r_names = _extract_levels_and_names(payload.get("researches"))
    specs = {str(k): int(v or 0) for k, v in (payload.get("specs") or {}).items()}

    return GameState(
        universe_id=int(uni.get("id", 0) or 0),
        user_id=int(user.get("userID", 0) or 0),
        nickname=str(user.get("nickname", "")),
        timestamp=float(info.get("ts", 0.0) or 0.0),
        planets=tuple(planets),
        active_planet_id=planets[0].planet_id if planets else None,
        researches=r_levels,
        research_names=r_names,
        specs=specs,
    )


def _parse_cost_from_text(text: str) -> Resources:
    """Extract `Required: X Pig iron Y Metal Z Kryptonite W Spice` values."""
    req_match = re.search(r"Required:\s*(.*?)(?:Active Building|Upgrade|Build level|Not enough|Cancel|$)", text, re.IGNORECASE)
    segment = req_match.group(1) if req_match else text

    def _find_amt(label: str) -> int:
        m = re.search(rf"([\d.]+)\s*{label}", segment, re.IGNORECASE)
        return parse_si_int(m.group(1)) if m else 0

    return Resources(
        pig_iron=_find_amt(r"Pig\s+iron"),
        metal=_find_amt(r"Metal"),
        kryptonite=_find_amt(r"Kryptonite"),
        spice=_find_amt(r"Spice"),
    )


def _find_item_card_row(
    detail_anchor,
    detail_type: str,
    action_param: Optional[str],
    item_id: int,
) -> Any:
    """Walk upward from an item detail anchor to its single-item card container."""
    node = detail_anchor
    fallback = detail_anchor.parent
    detail_re = re.compile(rf"action=internalDetail&type={re.escape(detail_type)}&id=(\d+)")
    for _ in range(10):
        if node is None or node.parent is None:
            break
        node = node.parent
        other_ids = {
            int(m.group(1))
            for a in node.find_all("a", href=True)
            for m in [detail_re.search(a["href"])]
            if m
        }
        if len(other_ids) > 1:
            break
        fallback = node
        txt = node.get_text(" ", strip=True)
        has_req = "Required:" in txt
        has_action = bool(action_param and node.select_one(f'a[href*="{action_param}={item_id}"]') is not None)
        if has_req and (has_action or node.name in ("table", "tbody", "tr", "div")):
            return node
    return fallback


def _find_card_row(detail_anchor, building_id: int) -> Any:
    """Backward-compatible wrapper for building card traversal."""
    return _find_item_card_row(detail_anchor, "building", "buildingID", building_id)


def parse_building_queue(html: str) -> tuple[QueueJob, ...]:
    """Parse active and queued jobs from `Construction queue (...)` in `internalBuildings`."""
    soup = BeautifulSoup(html, "html.parser")
    queue_table = None
    for tbl in soup.find_all("table"):
        txt = " ".join(tbl.get_text(" ", strip=True).split())
        if "Construction queue" in txt:
            queue_table = tbl
            break
    if queue_table is None:
        return ()

    jobs: list[QueueJob] = []
    seen: set[tuple[str, int]] = set()
    for m in re.finditer(r"([A-Za-z][A-Za-z ]+?)\s*\(\s*Level\s+(\d+)\s*\)", queue_table.get_text(" ", strip=True)):
        bname = " ".join(m.group(1).split())
        lvl = int(m.group(2))
        key = (bname.lower(), lvl)
        if key in seen:
            continue
        seen.add(key)
        jobs.append(
            QueueJob(
                category="building",
                name=bname,
                building_id=BUILDING_ID_BY_NAME.get(bname.lower()),
                target_level=lvl,
            )
        )
    return tuple(jobs)


def parse_buildings_page(
    html: str,
    current_levels: Optional[dict[str, int]] = None,
) -> tuple[BuildingOffer, ...]:
    """Parse building cards, costs, and actionable build links from `internalBuildings`."""
    soup = BeautifulSoup(html, "html.parser")
    levels = current_levels or {}
    seen_ids: set[int] = set()
    offers: list[BuildingOffer] = []

    def _parse_card(card: Any, bid: int, name: str) -> None:
        card_text = " ".join(card.get_text(" ", strip=True).split()) if card else name
        cost = _parse_cost_from_text(card_text)
        build_link = (
            card.select_one(f'a[href*="action=internalBuildItem"][href*="buildingID={bid}"]')
            if card
            else None
        )
        cur_lvl = int(levels.get(f"b{bid}", 0))
        target_lvl = cur_lvl + 1

        if build_link is not None:
            link_text = " ".join(build_link.get_text(" ", strip=True).split())
            m_lvl = re.search(r"(\d+)", link_text)
            if m_lvl:
                target_lvl = int(m_lvl.group(1))
                cur_lvl = max(cur_lvl, target_lvl - 1)
            offers.append(
                BuildingOffer(
                    building_id=bid,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_build=True,
                    status_text=link_text,
                    build_href=build_link.get("href"),
                )
            )
        else:
            status = "Not enough resources"
            if "not enough resources" not in card_text.lower():
                if "in construction" in card_text.lower() or "cancel" in card_text.lower():
                    status = "In construction"
                else:
                    status = "Unavailable"
            offers.append(
                BuildingOffer(
                    building_id=bid,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_build=False,
                    status_text=status,
                    build_href=None,
                )
            )

    # Prefer explicit `div.build-card.b-<id>` blocks when present in live DOM
    for card in soup.select("div.build-card"):
        cls_id = next(
            (int(c[2:]) for c in (card.get("class") or []) if re.fullmatch(r"b-\d+", c)),
            None,
        )
        detail_a = card.select_one('a[href*="action=internalDetail&type=building&id="]')
        if cls_id is None and detail_a is not None:
            m_id = re.search(r"id=(\d+)", detail_a["href"])
            cls_id = int(m_id.group(1)) if m_id else None
        if cls_id is None or cls_id in seen_ids:
            continue
        name = " ".join(detail_a.get_text(" ", strip=True).split()) if detail_a else ""
        if not name:
            continue
        seen_ids.add(cls_id)
        _parse_card(card, cls_id, name)

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        m_id = re.search(r"action=internalDetail&type=building&id=(\d+)", href)
        if not m_id:
            continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if not name:
            continue
        bid = int(m_id.group(1))
        if bid in seen_ids:
            continue
        seen_ids.add(bid)
        card = _find_item_card_row(anchor, "building", "buildingID", bid)
        _parse_card(card, bid, name)

    return tuple(offers)


def parse_research_page(
    html: str,
    researches: Optional[dict[str, int]] = None,
) -> tuple[ResearchOffer, ...]:
    """Parse research cards, costs, active status, and `researchID` links from `internalResearch`."""
    soup = BeautifulSoup(html, "html.parser")
    levels = researches or {}
    seen_ids: set[int] = set()
    offers: list[ResearchOffer] = []

    for anchor in soup.find_all("a", href=True):
        m_id = re.search(r"action=internalDetail&type=research&id=(\d+)", anchor["href"])
        if not m_id:
            continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if not name:
            continue
        rid_num = int(m_id.group(1))
        if rid_num in seen_ids:
            continue
        seen_ids.add(rid_num)

        card = _find_item_card_row(anchor, "research", "researchID", rid_num)
        card_text = " ".join(card.get_text(" ", strip=True).split()) if card else name
        cost = _parse_cost_from_text(card_text)
        rkey = f"r{rid_num}"
        cur_lvl = int(levels.get(rkey, 0))
        target_lvl = cur_lvl + 1

        res_link = card.select_one(f'a[href*="internalBuildItem"][href*="researchID={rid_num}"]') if card else None
        card_lower = card_text.lower()
        is_active = any(tok in card_lower for tok in ("cancel", "abort", "in research"))

        if res_link is not None:
            link_text = " ".join(res_link.get_text(" ", strip=True).split())
            m_lvl = re.search(r"(\d+)", link_text)
            if m_lvl:
                target_lvl = int(m_lvl.group(1))
                cur_lvl = max(cur_lvl, target_lvl - 1)
            offers.append(
                ResearchOffer(
                    research_id=rid_num,
                    research_key=rkey,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_research=True,
                    status_text=link_text,
                    research_href=res_link.get("href"),
                    active=is_active,
                )
            )
        else:
            status = "In research" if is_active else ("Not enough resources" if "not enough" in card_lower else "Unavailable")
            offers.append(
                ResearchOffer(
                    research_id=rid_num,
                    research_key=rkey,
                    name=name,
                    current_level=cur_lvl,
                    target_level=target_lvl,
                    cost=cost,
                    can_research=False,
                    status_text=status,
                    research_href=None,
                    active=is_active,
                )
            )

    return tuple(offers)


def parse_unit_factory_page(
    html: str,
    *,
    unit_type: str = "ship",
    owned_counts: Optional[dict[str, int]] = None,
) -> tuple[UnitOffer, ...]:
    """Parse unit build cards from `internalWeaponFactory` (`ship`) or `internalDefense` (`defense`)."""
    soup = BeautifulSoup(html, "html.parser")
    owned_map = owned_counts or {}
    detail_type = "defense" if unit_type == "defense" else "ship"
    seen_ids: set[int] = set()
    offers: list[UnitOffer] = []
    prefix = "d" if detail_type == "defense" else "s"

    for anchor in soup.find_all("a", href=True):
        m_id = re.search(rf"action=internalDetail&type={detail_type}&id=(\d+)", anchor["href"])
        if not m_id:
            continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if not name:
            continue
        uid = int(m_id.group(1))
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        card = _find_item_card_row(anchor, detail_type, None, uid)
        card_text = " ".join(card.get_text(" ", strip=True).split()) if card else name
        cost = _parse_cost_from_text(card_text)

        owned = int(owned_map.get(f"{prefix}{uid}", 0))
        m_own = re.search(r"(?:Available|On planet|Currently)\s*:\s*([\d.]+)", card_text, re.IGNORECASE)
        if m_own and owned == 0:
            owned = parse_si_int(m_own.group(1))

        input_name: Optional[str] = None
        if card:
            for inp in card.find_all("input"):
                itype = (inp.get("type") or "text").lower()
                iname = inp.get("name") or ""
                if itype in ("text", "number") and iname and iname not in ("sid", "packamount"):
                    if not inp.has_attr("disabled"):
                        input_name = str(iname)
                    break

        can_build = input_name is not None
        status = "Buildable" if can_build else "Locked / insufficient"
        offers.append(
            UnitOffer(
                unit_type=detail_type,
                unit_id=uid,
                name=name,
                owned=owned,
                cost=cost,
                can_build=can_build,
                input_name=input_name,
                status_text=status,
            )
        )

    return tuple(offers)


def parse_tutorial_from_home(html: str) -> Optional[TutorialStatus]:
    """Parse tutorial banner step and task lines from `internalHome` or `internalBuildings`."""
    soup = BeautifulSoup(html, "html.parser")
    full_text = " ".join(soup.get_text(" ", strip=True).split())
    step_m = re.search(r"Tutorial\s+(\d+)\s*/\s*(\d+)", full_text)
    if not step_m:
        return None

    step = int(step_m.group(1))
    total = int(step_m.group(2))

    tut_div = soup.find(id="tutorial.text")
    tut_text = " ".join(tut_div.get_text(" ", strip=True).split()) if tut_div else full_text

    def _resolve_ship_id(slug_or_name: str) -> Optional[int]:
        unit = game_data.resolve_unit("ships", slug_or_name)
        if not unit:
            return None
        from spaceinvasion.fleet import SHIP_SLUG_BY_KEY

        slug = str(unit.get("slug") or "")
        for skey, sslug in SHIP_SLUG_BY_KEY.items():
            if sslug == slug and skey.startswith("s") and skey[1:].isdigit():
                return int(skey[1:])
        return None

    def _classify_task(label: str, cur: int, tgt: int) -> TutorialTask:
        desc = f"{label} {cur} / {tgt}"
        inc_m = re.match(r"Increase your\s+([A-Za-z ]+?)\s+level$", label, re.IGNORECASE)
        if inc_m:
            bname = " ".join(inc_m.group(1).split())
            return TutorialTask(
                description=desc,
                building_name=bname,
                building_id=BUILDING_ID_BY_NAME.get(bname.lower()),
                current=cur,
                target=tgt,
                category="building",
            )
        upg_m = re.match(r"Upgrade your\s+([A-Za-z ]+?)$", label, re.IGNORECASE)
        if upg_m:
            rname = " ".join(upg_m.group(1).split())
            unit = game_data.resolve_unit("research", rname)
            rid = str(unit["id"]) if unit and unit.get("id") else None
            return TutorialTask(
                description=desc,
                building_name=rname,
                building_id=None,
                current=cur,
                target=tgt,
                category="research",
                research_id=rid,
            )
        bld_ship_m = re.match(r"Build\s+(?:an?\s+)?([A-Za-z ]+?)$", label, re.IGNORECASE)
        if bld_ship_m:
            sname = " ".join(bld_ship_m.group(1).split())
            unit = game_data.resolve_unit("ships", sname)
            if unit:
                return TutorialTask(
                    description=desc,
                    building_name=str(unit["name"]),
                    building_id=_resolve_ship_id(str(unit["slug"])),
                    current=cur,
                    target=tgt,
                    category="ship",
                )
        return TutorialTask(
            description=desc,
            building_name=label,
            building_id=None,
            current=cur,
            target=tgt,
            category="milestone",
        )

    tasks: list[TutorialTask] = []
    # Pattern matches lines like "Increase your Fusion plant level 0 / 3"
    for m in re.finditer(
        r"(Increase your\s+([A-Za-z ]+?)\s+level\s+(\d+)\s*/\s*(\d+))",
        full_text,
        re.IGNORECASE,
    ):
        tasks.append(_classify_task(f"Increase your {m.group(2).strip()} level", int(m.group(3)), int(m.group(4))))

    # Tutorial 4+ research lines like "Upgrade your Energy technology 0 / 2"
    for m in re.finditer(
        r"(Upgrade your\s+([A-Za-z ]+?)\s+(\d+)\s*/\s*(\d+))",
        full_text,
        re.IGNORECASE,
    ):
        tasks.append(_classify_task(f"Upgrade your {m.group(2).strip()}", int(m.group(3)), int(m.group(4))))

    # Tutorial ship build lines like "Build a Small transport 1 / 2"
    for m in re.finditer(
        r"(Build\s+(?:an?\s+)?([A-Za-z ]+?)\s+(\d+)\s*/\s*(\d+))",
        full_text,
        re.IGNORECASE,
    ):
        sname = " ".join(m.group(2).split())
        if game_data.resolve_unit("ships", sname):
            tasks.append(_classify_task(f"Build a {sname}", int(m.group(3)), int(m.group(4))))

    # Parse structured 2-column task rows inside `#tutorial.text` (captures combat/fleet milestones)
    if tut_div is not None:
        for tr in tut_div.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) != 2:
                continue
            label = " ".join(tds[0].get_text(" ", strip=True).split())
            cnt_txt = " ".join(tds[1].get_text(" ", strip=True).split())
            cnt_m = re.match(r"^(\d+)\s*/\s*(\d+)$", cnt_txt)
            if label and cnt_m:
                tasks.append(_classify_task(label, int(cnt_m.group(1)), int(cnt_m.group(2))))

    # Deduplicate identical task strings if duplicated across tutorial summary & body
    deduped: list[TutorialTask] = []
    seen_desc: set[str] = set()
    for t in tasks:
        key = t.description.lower()
        if key not in seen_desc:
            seen_desc.add(key)
            deduped.append(t)

    reward_m = re.search(
        r"You receive the following resources:\s*([^\n]+?)(?:$|Active|Pig iron mine)",
        tut_text,
    )
    reward_text = reward_m.group(1).strip() if reward_m else ""
    can_complete = soup.select_one('input[name="tutorialComplete"]') is not None

    return TutorialStatus(
        step=step,
        total_steps=total,
        tasks=tuple(deduped),
        reward_text=reward_text,
        can_complete=can_complete,
    )


def parse_home_metadata(html: str) -> tuple[Optional[int], Optional[float], bool, tuple[QueueJob, ...], tuple[FleetAlert, ...]]:
    """Extract active planet ID, speedup factor, newbie protection, queues, and fleet alerts."""
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())

    active_planet_id: Optional[int] = None
    m_pid = re.search(r"planetID\s*:\s*(\d+)", html)
    if m_pid:
        active_planet_id = int(m_pid.group(1))

    speedup: Optional[float] = None
    m_spd = re.search(r"active factor:\s*x([0-9.]+)", text, re.IGNORECASE)
    if m_spd:
        speedup = float(m_spd.group(1))

    newbie = "no attacks can be launched on you" in text.lower()

    queues: list[QueueJob] = []
    for box_id, cat in (
        ("sp-own-building", "building"),
        ("sp-own-research", "research"),
        ("sp-own-fleet", "fleet"),
    ):
        el = soup.find(id=box_id)
        if el:
            content = " ".join(el.get_text(" ", strip=True).split())
            if content:
                queues.append(QueueJob(category=cat, name=content))

    alerts: list[FleetAlert] = []
    # Check for hostile attack indicators rendered on overview
    for tag in soup.select('[class*="attack"], [id*="attack"]'):
        msg = " ".join(tag.get_text(" ", strip=True).split())
        if msg and "no attacks can be launched on you" not in msg.lower() and "beware of the attacks" not in msg.lower():
            alerts.append(FleetAlert(mission="ATTACK", hostile=True, summary=msg[:200]))

    return active_planet_id, speedup, newbie, tuple(queues), tuple(alerts)


_MENU_POINTS_RANK_RE = re.compile(
    r"\b(?:Points|Punkte|Pontos)\s*[:\-]?\s*([\d.]+)(?:\s*(?:#|Rank|Platz|Pos)\s*[:\-]?\s*([\d.]+))?",
    re.IGNORECASE,
)


def parse_menu_status(html: str) -> tuple[dict[str, int], Optional[int], Optional[int]]:
    """Extract unread message counts, server points, and rank from `F_Navi` (`internalMenu`)."""
    soup = BeautifulSoup(html, "html.parser")
    counts: dict[str, int] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "action=internalMessages" not in href:
            continue
        m_type = re.search(r"type=([a-z]+)", href)
        key = m_type.group(1) if m_type else "total"
        txt = " ".join(anchor.get_text(" ", strip=True).split())
        m_num = re.search(r"(\d+)", txt)
        counts[key] = int(m_num.group(1)) if m_num else 0

    full_text = " ".join(soup.get_text(" ", strip=True).split())
    points: Optional[int] = None
    rank: Optional[int] = None
    m_stat = _MENU_POINTS_RANK_RE.search(full_text)
    if m_stat:
        points = parse_si_int(m_stat.group(1))
        if m_stat.group(2):
            rank = parse_si_int(m_stat.group(2))
    return counts, points, rank


def parse_economy_rates(html: str) -> dict[str, int]:
    """Extract per-building percentage throttle values (`resource1`..`resource5`) from `internalEconomy`."""
    soup = BeautifulSoup(html, "html.parser")
    rates: dict[str, int] = {}
    for sel in soup.find_all("select", attrs={"name": re.compile(r"^resource\d+$")}):
        name = str(sel.get("name") or "")
        selected = sel.find("option", selected=True)
        if selected is None:
            options = sel.find_all("option")
            selected = options[0] if options else None
        if selected is not None:
            raw_val = str(selected.get("value") or selected.get_text(strip=True))
            rates[name] = parse_si_int(raw_val)
    return rates


def parse_menu_messages(html: str) -> dict[str, int]:
    """Extract unread message counts per category from `F_Navi` (`internalMenu`)."""
    counts, _, _ = parse_menu_status(html)
    return counts


def game_state_from_dict(data: dict[str, Any]) -> GameState:
    """Reconstruct a typed `GameState` from serialized JSON (e.g. `GET /state`)."""
    planets: list[PlanetState] = []
    for raw_p in data.get("planets", []):
        res_d = raw_p.get("resources") or {}
        sto_d = raw_p.get("storage") or {}
        eng_d = raw_p.get("energy") or {}
        offers = tuple(
            BuildingOffer(
                building_id=int(o["building_id"]),
                name=str(o["name"]),
                current_level=int(o["current_level"]),
                target_level=int(o["target_level"]),
                cost=Resources(**(o.get("cost") or {})),
                can_build=bool(o["can_build"]),
                status_text=str(o.get("status_text", "")),
                build_href=o.get("build_href"),
            )
            for o in raw_p.get("building_offers", [])
        )
        ship_offers = tuple(
            UnitOffer(
                unit_type=str(u.get("unit_type", "ship")),
                unit_id=int(u["unit_id"]),
                name=str(u["name"]),
                owned=int(u.get("owned", 0)),
                cost=Resources(**(u.get("cost") or {})),
                can_build=bool(u["can_build"]),
                input_name=u.get("input_name"),
                status_text=str(u.get("status_text", "")),
            )
            for u in raw_p.get("ship_offers", [])
        )
        defense_offers = tuple(
            UnitOffer(
                unit_type=str(u.get("unit_type", "defense")),
                unit_id=int(u["unit_id"]),
                name=str(u["name"]),
                owned=int(u.get("owned", 0)),
                cost=Resources(**(u.get("cost") or {})),
                can_build=bool(u["can_build"]),
                input_name=u.get("input_name"),
                status_text=str(u.get("status_text", "")),
            )
            for u in raw_p.get("defense_offers", [])
        )
        planets.append(
            PlanetState(
                planet_id=int(raw_p["planet_id"]),
                name=str(raw_p.get("name", "")),
                coords=str(raw_p.get("coords", "")),
                planet_type=str(raw_p.get("planet_type", "normal")),
                temperature=int(raw_p.get("temperature", 0)),
                fields_used=int(raw_p.get("fields_used", 0)),
                fields_max=int(raw_p.get("fields_max", 0)),
                resources=Resources(**res_d),
                storage=Resources(**sto_d),
                energy=EnergyBalance(**eng_d),
                buildings={str(k): int(v) for k, v in (raw_p.get("buildings") or {}).items()},
                building_names={str(k): str(v) for k, v in (raw_p.get("building_names") or {}).items()},
                ships={str(k): int(v) for k, v in (raw_p.get("ships") or {}).items()},
                defense={str(k): int(v) for k, v in (raw_p.get("defense") or {}).items()},
                building_offers=offers,
                ship_offers=ship_offers,
                defense_offers=defense_offers,
                economy_rates={str(k): int(v) for k, v in (raw_p.get("economy_rates") or {}).items()},
            )
        )

    raw_tut = data.get("tutorial")
    tutorial: Optional[TutorialStatus] = None
    if isinstance(raw_tut, dict):
        tasks = tuple(
            TutorialTask(
                description=str(t["description"]),
                building_name=t.get("building_name"),
                building_id=t.get("building_id"),
                current=int(t.get("current", 0)),
                target=int(t.get("target", 0)),
                category=str(t.get("category", "building")),
                research_id=t.get("research_id"),
            )
            for t in raw_tut.get("tasks", [])
        )
        tutorial = TutorialStatus(
            step=int(raw_tut["step"]),
            total_steps=int(raw_tut["total_steps"]),
            tasks=tasks,
            reward_text=str(raw_tut.get("reward_text", "")),
            can_complete=bool(raw_tut.get("can_complete", False)),
        )

    queues = tuple(
        QueueJob(
            category=str(q["category"]),
            name=str(q["name"]),
            building_id=q.get("building_id"),
            target_level=q.get("target_level"),
            remaining_text=str(q.get("remaining_text", "")),
        )
        for q in data.get("active_queues", [])
    )
    alerts = tuple(
        FleetAlert(
            mission=str(a["mission"]),
            hostile=bool(a["hostile"]),
            summary=str(a["summary"]),
        )
        for a in data.get("fleet_alerts", [])
    )

    res_offers = tuple(
        ResearchOffer(
            research_id=int(r["research_id"]),
            research_key=str(r["research_key"]),
            name=str(r["name"]),
            current_level=int(r.get("current_level", 0)),
            target_level=int(r.get("target_level", 1)),
            cost=Resources(**(r.get("cost") or {})),
            can_research=bool(r["can_research"]),
            status_text=str(r.get("status_text", "")),
            research_href=r.get("research_href"),
            active=bool(r.get("active", False)),
        )
        for r in data.get("research_offers", [])
    )

    return GameState(
        universe_id=int(data.get("universe_id", 0)),
        user_id=int(data.get("user_id", 0)),
        nickname=str(data.get("nickname", "")),
        timestamp=float(data.get("timestamp", 0.0)),
        planets=tuple(planets),
        active_planet_id=data.get("active_planet_id"),
        researches={str(k): int(v) for k, v in (data.get("researches") or {}).items()},
        research_names={str(k): str(v) for k, v in (data.get("research_names") or {}).items()},
        specs={str(k): int(v) for k, v in (data.get("specs") or {}).items()},
        unread_messages={str(k): int(v) for k, v in (data.get("unread_messages") or {}).items()},
        tutorial=tutorial,
        active_queues=queues,
        fleet_alerts=alerts,
        research_offers=res_offers,
        speedup_factor=data.get("speedup_factor"),
        newbie_protection=bool(data.get("newbie_protection", False)),
        account_points=data.get("account_points"),
        account_rank=data.get("account_rank"),
    )
