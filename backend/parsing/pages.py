"""Parsers for buildings, research, weapons factory / defense, and economy pages."""

from __future__ import annotations

from collections.abc import Mapping
import re

from bs4 import BeautifulSoup, Tag

from backend.core.coercion import coerce_bounded_int, parse_game_int
from backend.core.model import BuildingOffer, ResearchOffer, Resources, UnitOffer
from backend.parsing._common import attr_opt_str, attr_str, attr_tokens, safe_text, tag_text
from backend.parsing.queues import parse_building_queue, parse_research_queue_timer


def _parse_cost_from_text(text: object) -> Resources:
    text_s = safe_text(text)
    if not text_s:
        return Resources()
    req_match = re.search(
        r"Required:\s*(.*?)(?:Active Building|Upgrade|Build level|Not enough|Cancel|$)",
        text_s,
        re.IGNORECASE,
    )
    segment = req_match.group(1) if req_match else text_s

    def _find_amt(label: str) -> int:
        match = re.search(rf"([\d.]+)\s*{label}", segment, re.IGNORECASE | re.ASCII)
        return parse_game_int(match.group(1)) if match else 0

    return Resources(
        pig_iron=_find_amt(r"Pig\s+iron"),
        metal=_find_amt(r"Metal"),
        kryptonite=_find_amt(r"Kryptonite"),
        spice=_find_amt(r"Spice"),
    )


def _find_item_card_row(
    detail_anchor: Tag,
    detail_type: str,
    action_param: str | None,
    item_id: int,
) -> Tag | None:
    node: Tag | None = detail_anchor
    fallback: Tag | None = detail_anchor.parent if isinstance(detail_anchor.parent, Tag) else None
    detail_re = re.compile(rf"action=internalDetail&type={re.escape(detail_type)}&id=(\d+)", re.ASCII)
    for _ in range(10):
        if node is None or not isinstance(node.parent, Tag):
            break
        node = node.parent
        other_ids = {
            m.group(1).lstrip("0") or "0"
            for anchor in node.find_all("a", href=True)
            if isinstance(anchor, Tag)
            for m in (detail_re.search(attr_str(anchor, "href")),)
            if m
        }
        if len(other_ids) > 1:
            break
        fallback = node
        has_req = "Required:" in tag_text(node)
        has_action = bool(action_param and node.select_one(f'a[href*="{action_param}={item_id}"]') is not None)
        if has_req and (has_action or node.name in ("table", "tbody", "tr", "div")):
            return node
    return fallback


def _build_offer_from_card(card: Tag | None, bid: int, name: str, levels: Mapping[str, int]) -> BuildingOffer:
    card_text = tag_text(card) if card is not None else name
    cost = _parse_cost_from_text(card_text)
    build_link = card.select_one(f'a[href*="action=internalBuildItem"][href*="buildingID={bid}"]') if card else None
    cur_lvl = max(0, coerce_bounded_int(levels.get(f"b{bid}", 0), 0))
    target_lvl = cur_lvl + 1
    if isinstance(build_link, Tag):
        link_text = tag_text(build_link)
        m_lvl = re.search(r"(\d+)", link_text, re.ASCII)
        if m_lvl:
            parsed_tgt = coerce_bounded_int(m_lvl.group(1), -1)
            if parsed_tgt >= 0:
                target_lvl = parsed_tgt
                cur_lvl = max(cur_lvl, target_lvl - 1)
        return BuildingOffer(
            building_id=bid,
            name=name,
            current_level=cur_lvl,
            target_level=target_lvl,
            cost=cost,
            can_build=True,
            status_text=link_text,
            build_href=attr_opt_str(build_link, "href"),
        )
    low_card = card_text.lower()
    status = (
        "Not enough resources"
        if "not enough resources" in low_card
        else ("In construction" if ("in construction" in low_card or "cancel" in low_card) else "Unavailable")
    )
    return BuildingOffer(
        building_id=bid,
        name=name,
        current_level=cur_lvl,
        target_level=target_lvl,
        cost=cost,
        can_build=False,
        status_text=status,
        build_href=None,
    )


def parse_buildings_page(
    html: str,
    current_levels: Mapping[str, int] | None = None,
) -> tuple[BuildingOffer, ...]:
    """Parse building cards, costs, and actionable build links from ``internalBuildings``."""
    soup = BeautifulSoup(html, "html.parser")
    levels: Mapping[str, int] = current_levels or {}
    seen_ids: set[int] = set()
    offers: list[BuildingOffer] = []

    for card in soup.select("div.build-card"):
        cls_id = next(
            (coerce_bounded_int(c[2:], -1) for c in attr_tokens(card, "class") if re.fullmatch(r"b-\d+", c, re.ASCII)),
            None,
        )
        if cls_id is not None and cls_id < 0:
            cls_id = None
        detail_a = card.select_one('a[href*="action=internalDetail&type=building&id="]')
        if cls_id is None and isinstance(detail_a, Tag):
            m_id = re.search(r"id=(\d+)", attr_str(detail_a, "href"), re.ASCII)
            parsed_cls = coerce_bounded_int(m_id.group(1), -1) if m_id else -1
            cls_id = parsed_cls if parsed_cls >= 0 else None
        if cls_id is None or cls_id in seen_ids:
            continue
        name = tag_text(detail_a) if isinstance(detail_a, Tag) else ""
        if not name:
            continue
        seen_ids.add(cls_id)
        offers.append(_build_offer_from_card(card, cls_id, name, levels))

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        m_id = re.search(r"action=internalDetail&type=building&id=(\d+)", attr_str(anchor, "href"), re.ASCII)
        if not m_id:
            continue
        name = tag_text(anchor)
        if not name:
            continue
        bid = coerce_bounded_int(m_id.group(1), -1)
        if bid < 0 or bid in seen_ids:
            continue
        seen_ids.add(bid)
        offers.append(
            _build_offer_from_card(_find_item_card_row(anchor, "building", "buildingID", bid), bid, name, levels)
        )
    return tuple(offers)


def parse_research_page(
    html: str,
    researches: Mapping[str, int] | None = None,
) -> tuple[ResearchOffer, ...]:
    """Parse research cards, costs, active status, and ``researchID`` links from ``internalResearch``."""
    soup = BeautifulSoup(html, "html.parser")
    levels: Mapping[str, int] = researches or {}
    seen_ids: set[int] = set()
    offers: list[ResearchOffer] = []

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        m_id = re.search(r"action=internalDetail&type=research&id=(\d+)", attr_str(anchor, "href"), re.ASCII)
        if not m_id:
            continue
        name = tag_text(anchor)
        if not name:
            continue
        rid_num = coerce_bounded_int(m_id.group(1), -1)
        if rid_num < 0 or rid_num in seen_ids:
            continue
        seen_ids.add(rid_num)
        card = _find_item_card_row(anchor, "research", "researchID", rid_num)
        card_text = tag_text(card) if card is not None else name
        cost = _parse_cost_from_text(card_text)
        rkey = f"r{rid_num}"
        cur_lvl = max(0, coerce_bounded_int(levels.get(rkey, 0), 0))
        target_lvl = cur_lvl + 1
        res_link = card.select_one(f'a[href*="internalBuildItem"][href*="researchID={rid_num}"]') if card else None
        card_lower = card_text.lower()
        is_active = any(tok in card_lower for tok in ("cancel", "abort", "in research"))
        if isinstance(res_link, Tag):
            link_text = tag_text(res_link)
            m_lvl = re.search(r"(\d+)", link_text, re.ASCII)
            if m_lvl:
                parsed_tgt = coerce_bounded_int(m_lvl.group(1), -1)
                if parsed_tgt >= 0:
                    target_lvl = parsed_tgt
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
                    research_href=attr_opt_str(res_link, "href"),
                    active=is_active,
                )
            )
        else:
            status = (
                "In research"
                if is_active
                else ("Not enough resources" if "not enough" in card_lower else "Unavailable")
            )
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
    owned_counts: Mapping[str, int] | None = None,
) -> tuple[UnitOffer, ...]:
    """Parse unit build cards from ``internalWeaponFactory`` (``ship``) or ``internalDefense`` (``defense``)."""
    soup = BeautifulSoup(html, "html.parser")
    owned_map: Mapping[str, int] = owned_counts or {}
    detail_type = "defense" if unit_type == "defense" else "ship"
    prefix = "d" if detail_type == "defense" else "s"
    seen_ids: set[int] = set()
    offers: list[UnitOffer] = []

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        m_id = re.search(rf"action=internalDetail&type={detail_type}&id=(\d+)", attr_str(anchor, "href"), re.ASCII)
        if not m_id:
            continue
        name = tag_text(anchor)
        if not name:
            continue
        uid = coerce_bounded_int(m_id.group(1), -1)
        if uid < 0 or uid in seen_ids:
            continue
        seen_ids.add(uid)
        card = _find_item_card_row(anchor, detail_type, None, uid)
        card_text = tag_text(card) if card is not None else name
        cost = _parse_cost_from_text(card_text)
        owned = max(0, coerce_bounded_int(owned_map.get(f"{prefix}{uid}", 0), 0))
        m_own = re.search(r"(?:Available|On planet|Currently)\s*:\s*([\d.]+)", card_text, re.IGNORECASE | re.ASCII)
        if m_own and owned == 0:
            owned = parse_game_int(m_own.group(1))
        input_name: str | None = None
        if card is not None:
            for inp in card.find_all("input"):
                if not isinstance(inp, Tag):
                    continue
                itype = (attr_opt_str(inp, "type") or "text").lower()
                iname = attr_str(inp, "name")
                if itype in ("text", "number") and iname and iname not in ("sid", "packamount"):
                    if not inp.has_attr("disabled"):
                        input_name = iname
                    break
        can_build = input_name is not None
        offers.append(
            UnitOffer(
                unit_type=detail_type,
                unit_id=uid,
                name=name,
                owned=owned,
                cost=cost,
                can_build=can_build,
                input_name=input_name,
                status_text="Buildable" if can_build else "Locked / insufficient",
            )
        )
    return tuple(offers)


def parse_economy_rates(html: str) -> dict[str, int]:
    """Extract per-building percentage throttle values (``resource1``..``resource6``) from ``internalEconomy``."""
    soup = BeautifulSoup(html, "html.parser")
    rates: dict[str, int] = {}
    for sel in soup.find_all("select", attrs={"name": re.compile(r"^resource\d+$", re.ASCII)}):
        if not isinstance(sel, Tag):
            continue
        name = attr_str(sel, "name")
        selected: Tag | None = sel.find("option", selected=True)
        if selected is None:
            options = [opt for opt in sel.find_all("option") if isinstance(opt, Tag)]
            selected = options[0] if options else None
        if selected is not None:
            raw_val = attr_opt_str(selected, "value") or selected.get_text(strip=True)
            rates[name] = parse_game_int(raw_val)
    return rates


__all__ = [
    "parse_building_queue",
    "parse_buildings_page",
    "parse_economy_rates",
    "parse_research_page",
    "parse_research_queue_timer",
    "parse_unit_factory_page",
]
