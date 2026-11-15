"""Galaxy page system-coordinate inference and universe-bounds parsers."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
import json
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from backend.core.coords import ARROW_NAV_MAX_DISTANCE as ARROW_NAV_MAX_DISTANCE
from backend.parsing._common import (
    COORD_CONTEXT_PREFIX_RE,
    GALAXY_COORDS_TRIPLE_RE,
    GALAXY_PAIR_NOISE_PREFIX_RE,
    GALAXY_TRIPLE_NOISE_SUFFIX_RE,
    attr_opt_str,
    attr_str,
    collapse_ws,
    is_clock_0m_triple,
    is_clock_shaped_triple,
    is_parenthesized_noise_triple,
    valid_galaxy,
    valid_system,
)

_GALAXY_BRACKETED_PAIR_RE = re.compile(r"\[\s*(\d{1,2})\s*[:/]\s*(\d{1,3})\s*\](?!\s*[:/]\s*\d)", re.ASCII)
_GALAXY_HEADER_PAIR_RE = re.compile(
    r"(?:Galaxy|Solar\s*System|System|Sonnensystem)\s*(?::\s*)?\[?\s*(\d{1,2})\s*[:/]\s*(\d{1,3})(?!\d)\s*\]?(?!\s*[:/]\s*\d)",
    re.IGNORECASE | re.ASCII,
)
_SI_TPL_GAL_RE = re.compile(
    r"""(?:^|[,{\s.;])["']?(?:galaxy|gal)["']?\s*[:=]\s*["']?(\d{1,2})["']?(?![0-9])""",
    re.ASCII,
)
_SI_TPL_SYS_RE = re.compile(
    r"""(?:^|[,{\s.;])["']?(?:sun|system)["']?\s*[:=]\s*["']?(\d{1,3})["']?(?![0-9])""",
    re.ASCII,
)


def _coords_from_json_element(soup: BeautifulSoup) -> tuple[int, int] | None:
    json_el = soup.find(id="json-element")
    if not isinstance(json_el, Tag):
        return None
    raw_jtxt = (json_el.string if json_el.string is not None else json_el.get_text("", strip=True)) or ""
    if not raw_jtxt.strip():
        return None
    try:
        parsed_j: object = json.loads(raw_jtxt)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed_j, Mapping):
        return None
    pos_j = parsed_j.get("position")
    src_pos: Mapping[object, object] = pos_j if isinstance(pos_j, Mapping) else parsed_j
    jg = valid_galaxy(src_pos.get("galaxy", src_pos.get("gal")))
    js = valid_system(src_pos.get("system", src_pos.get("sun")))
    return (jg, js) if (jg is not None and js is not None) else None


def _coords_from_form_controls(soup: BeautifulSoup) -> tuple[int | None, int | None]:
    gal_found: int | None = None
    sys_found: int | None = None
    for inp in soup.find_all("input"):
        if not isinstance(inp, Tag):
            continue
        name = (attr_str(inp, "name") or attr_str(inp, "id")).strip().lower()
        val = inp.get("value")
        if val is None:
            continue
        if name in ("galaxy", "gal", "g") and gal_found is None:
            gal_found = valid_galaxy(val)
        elif name in ("system", "sun", "s") and sys_found is None:
            sys_found = valid_system(val)

    if gal_found is None or sys_found is None:
        for sel in soup.find_all("select"):
            if not isinstance(sel, Tag):
                continue
            name = (attr_str(sel, "name") or attr_str(sel, "id")).strip().lower()
            opt = sel.find("option", selected=True) or sel.find("option", attrs={"selected": True})
            if not isinstance(opt, Tag):
                continue
            raw_val: object = opt.get("value") if opt.get("value") is not None else opt.get_text(strip=True)
            if name in ("galaxy", "gal", "g") and gal_found is None:
                gal_found = valid_galaxy(raw_val)
            elif name in ("system", "sun", "s") and sys_found is None:
                sys_found = valid_system(raw_val)
    return gal_found, sys_found


def _coords_from_si_tpl(html: str, gal_found: int | None, sys_found: int | None) -> tuple[int | None, int | None]:
    if not isinstance(html, str) or "SI_TPL.galaxy" not in html:
        return gal_found, sys_found
    m_block = re.search(r"SI_TPL\.galaxy\s*=\s*\{([^}]*)\}", html, re.ASCII)
    idx = html.find("SI_TPL.galaxy")
    scope = m_block.group(1) if m_block is not None else html[idx : idx + 512]
    m_g = _SI_TPL_GAL_RE.search(scope)
    m_s = _SI_TPL_SYS_RE.search(scope)
    if m_g and gal_found is None:
        gal_found = valid_galaxy(m_g.group(1))
    if m_s and sys_found is None:
        sys_found = valid_system(m_s.group(1))
    return gal_found, sys_found


def _build_text_with_link_spans(soup: BeautifulSoup) -> tuple[str, list[tuple[int, int]]]:
    text_parts: list[str] = []
    link_spans: list[tuple[int, int]] = []
    pos = 0
    last_a_id: int | None = None
    for s in soup.strings:
        cleaned = collapse_ws(str(s))
        if not cleaned:
            continue
        a_tag = s.find_parent("a")
        a_id = id(a_tag) if a_tag is not None else None
        if text_parts:
            pos += 1
        start = pos
        end = start + len(cleaned)
        text_parts.append(cleaned)
        pos = end
        if a_id is not None:
            if link_spans and last_a_id == a_id:
                link_spans[-1] = (link_spans[-1][0], end)
            else:
                link_spans.append((start, end))
        last_a_id = a_id
    return " ".join(text_parts), link_spans


def _coords_from_header_or_brackets(text: str) -> tuple[int, int] | None:
    m_hdr = _GALAXY_HEADER_PAIR_RE.search(text)
    if m_hdr:
        hg, hs = valid_galaxy(m_hdr.group(1)), valid_system(m_hdr.group(2))
        if hg is not None and hs is not None:
            return hg, hs
    for m_br in _GALAXY_BRACKETED_PAIR_RE.finditer(text):
        if GALAXY_PAIR_NOISE_PREFIX_RE.search(text[: m_br.start()]) or GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(
            text[m_br.end() :]
        ):
            continue
        g_s, s_s = m_br.group(1), m_br.group(2)
        hg, hs = valid_galaxy(g_s), valid_system(s_s)
        if hg is None or hs is None:
            continue
        is_clock_pair = len(g_s) == 2 and len(s_s) == 2 and 0 <= hg <= 23 and 0 <= hs <= 59
        if is_clock_pair and not COORD_CONTEXT_PREFIX_RE.search(text[: m_br.start()]):
            continue
        return hg, hs
    return None


def _coords_from_triples(text: str, link_spans: list[tuple[int, int]]) -> tuple[int, int] | None:
    in_link_set: set[tuple[int, int]] = set()
    clean_triples: list[tuple[tuple[int, int], bool, bool, bool, bool, bool]] = []
    clean_counts: Counter[tuple[int, int]] = Counter()
    distinct_pos: dict[tuple[int, int], set[int]] = defaultdict(set)
    non_clock_keys: set[tuple[int, int]] = set()

    for m_tr in GALAXY_COORDS_TRIPLE_RE.finditer(text):
        g_s, s_s, p_s = m_tr.group(1), m_tr.group(2), m_tr.group(3)
        tg, ts, tp = valid_galaxy(g_s), valid_system(s_s), int(p_s)
        if tg is None or ts is None or not (1 <= tp <= 16):
            continue
        start, end = m_tr.start(), m_tr.end()
        if (
            is_parenthesized_noise_triple(text, start, end)
            or GALAXY_PAIR_NOISE_PREFIX_RE.search(text[:start])
            or GALAXY_TRIPLE_NOISE_SUFFIX_RE.match(text[end:])
        ):
            continue
        key = (tg, ts)
        clean_counts[key] += 1
        distinct_pos[key].add(tp)
        is_in_link = any(s_a <= start and end <= e_a for s_a, e_a in link_spans)
        if is_in_link:
            in_link_set.add(key)
        before_ch = text[start - 1] if start > 0 else ""
        after_ch = text[end] if end < len(text) else ""
        is_clock = is_clock_shaped_triple(g_s, s_s, p_s) or is_clock_0m_triple(g_s, s_s, p_s)
        if not is_clock:
            non_clock_keys.add(key)
        clean_triples.append(
            (
                key,
                is_in_link,
                before_ch == "[" and after_ch == "]",
                len(s_s) == 3,
                bool(COORD_CONTEXT_PREFIX_RE.search(text[:start])),
                is_clock,
            )
        )

    strong: Counter[tuple[int, int]] = Counter()
    strong_non_clock: Counter[tuple[int, int]] = Counter()
    ctx_counts: Counter[tuple[int, int]] = Counter()
    for key, is_in_link, is_br, is_3d, has_ctx, is_clock in clean_triples:
        if has_ctx:
            ctx_counts[key] += 1
        if is_in_link or key in in_link_set or (is_br and not is_clock) or is_3d or has_ctx:
            strong[key] += 1
            if not is_clock or key in non_clock_keys:
                strong_non_clock[key] += 1

    for key, cnt in clean_counts.items():
        if key not in strong and ((cnt >= 2 and key in non_clock_keys) or len(distinct_pos[key]) >= 2):
            strong[key] = cnt
            if key in non_clock_keys:
                strong_non_clock[key] = cnt

    for bucket in (strong_non_clock, ctx_counts, strong):
        if bucket:
            return bucket.most_common(1)[0][0]
    return None


def _coords_from_url(url: str, gal_found: int | None, sys_found: int | None) -> tuple[int | None, int | None]:
    if not url:
        return gal_found, sys_found
    qs = parse_qs(urlparse(url).query)
    if gal_found is None:
        for key in ("galaxy", "gal", "g"):
            if key in qs and qs[key]:
                gal_found = valid_galaxy(qs[key][0])
                if gal_found is not None:
                    break
    if sys_found is None:
        for key in ("system", "sun", "s"):
            if key in qs and qs[key]:
                sys_found = valid_system(qs[key][0])
                if sys_found is not None:
                    break
    return gal_found, sys_found


def extract_galaxy_system_coords(
    html: str,
    *,
    url: str = "",
    default: tuple[int, int] = (1, 1),
) -> tuple[int, int]:
    """Infer ``(galaxy, system)`` from Galaxy HTML JSON payload, inputs/selects/headers, or URL."""
    soup = BeautifulSoup(html or "", "html.parser")
    from_json = _coords_from_json_element(soup)
    if from_json is not None:
        return from_json

    gal_found, sys_found = _coords_from_form_controls(soup)
    if gal_found is not None and sys_found is not None:
        return gal_found, sys_found

    gal_found, sys_found = _coords_from_si_tpl(html, gal_found, sys_found)
    if gal_found is not None and sys_found is not None:
        return gal_found, sys_found

    for own_el in soup.select("#planet-coords, #planet-list"):
        own_el.decompose()
    text, link_spans = _build_text_with_link_spans(soup)

    from_hdr = _coords_from_header_or_brackets(text)
    if from_hdr is not None:
        return (gal_found or from_hdr[0]), (sys_found or from_hdr[1])

    from_triples = _coords_from_triples(text, link_spans)
    if from_triples is not None:
        return (gal_found or from_triples[0]), (sys_found or from_triples[1])

    gal_found, sys_found = _coords_from_url(url, gal_found, sys_found)
    return (
        gal_found if gal_found is not None else default[0],
        sys_found if sys_found is not None else default[1],
    )


def extract_galaxy_coords_from_html(
    html: str,
    url: str = "",
    *,
    default: tuple[int, int] = (1, 1),
) -> tuple[int, int]:
    """Alias for ``extract_galaxy_system_coords`` supporting positional ``url``."""
    return extract_galaxy_system_coords(html, url=url, default=default)


def extract_galaxy_universe_bounds(html: str) -> tuple[int | None, int | None]:
    """Extract ``(max_galaxies, max_system)`` from 2D Galaxy ``SI_TPL.galaxy`` script metadata."""
    if not isinstance(html, str) or not html:
        return None, None
    scope = ""
    tpl_idx = html.find("SI_TPL.galaxy")
    if tpl_idx != -1:
        end_idx = html.find("</script>", tpl_idx)
        scope = html[tpl_idx:end_idx] if end_idx != -1 else html[tpl_idx : tpl_idx + 65536]
    elif "maxGalaxies" in html or "maxSystem" in html:
        scope = html
    if not scope:
        return None, None

    max_gal: int | None = None
    max_sys: int | None = None
    m_gal = re.search(
        r"""(?:^|[,{\s.;])["']?maxGalaxies["']?\s*[:=]\s*["']?(\d{1,2})["']?(?![0-9])""",
        scope,
        re.ASCII,
    )
    if m_gal:
        g_val = int(m_gal.group(1))
        if 1 <= g_val <= 50:
            max_gal = g_val
    m_sys = re.search(
        r"""(?:^|[,{\s.;])["']?maxSystem["']?\s*[:=]\s*["']?(\d{1,4})["']?(?![0-9])""",
        scope,
        re.ASCII,
    )
    if m_sys:
        s_val = int(m_sys.group(1))
        if 1 <= s_val <= 1000:
            max_sys = s_val
    return max_gal, max_sys


__all__ = [
    "ARROW_NAV_MAX_DISTANCE",
    "attr_opt_str",
    "extract_galaxy_coords_from_html",
    "extract_galaxy_system_coords",
    "extract_galaxy_universe_bounds",
]
