"""JavaScript snippets executed inside `F_Hauptframe` for SpaceInvasion 2D Galaxy operations."""

from __future__ import annotations

from typing import Final

SYNC_GALAXY_JSON_JS: Final[str] = (
    "var je = document.getElementById('json-element');"
    "if (!je) return;"
    "var v = '';"
    "if (window.si && window.si.result && window.si.result.planets) {"
    "  try {"
    "    var rawPl = window.si.result.planets;"
    "    var outPl = {};"
    "    for (var pk in rawPl) {"
    "      if (!Object.prototype.hasOwnProperty.call(rawPl, pk)) continue;"
    "      var item = rawPl[pk];"
    "      if (item && typeof item === 'object') {"
    "        var copy = {};"
    "        for (var ik in item) {"
    "          if (Object.prototype.hasOwnProperty.call(item, ik) && typeof item[ik] !== 'function') {"
    "            copy[ik] = item[ik];"
    "          }"
    "        }"
    "        outPl[String(Number(pk) + (Array.isArray(rawPl) ? 1 : 0))] = copy;"
    "      }"
    "    }"
    "    var posObj = Object.assign({}, window.si.result.position || {});"
    "    if (!posObj.galaxy && window.si.galaxy) posObj.galaxy = Number(window.si.galaxy);"
    "    if (!posObj.system && !posObj.sun && window.si.sun) posObj.system = Number(window.si.sun);"
    "    v = JSON.stringify({position: posObj, planets: outPl});"
    "  } catch (e) { v = ''; }"
    "}"
    "if (!v || v === 'null') {"
    "  v = (typeof je.value === 'string') ? je.value.trim() : '';"
    "}"
    "if (v && v !== 'null') {"
    "  if (je.value !== v) je.value = v;"
    "  if (je.textContent !== v) je.textContent = v;"
    "}"
)

WAIT_GALAXY_AJAX_READY_JS: Final[str] = (
    "if (window.si) {"
    "  if (window.si._getStatus) return false;"
    "  if (window.si.result && window.si.result.position && window.si.result.planets) return true;"
    "}"
    "var gc = document.getElementById('galaxy-container');"
    "if (gc && gc.querySelector && gc.querySelector('.planet-hit')) return true;"
    "var je = document.getElementById('json-element');"
    "if (je && typeof je.value === 'string' && je.value.length > 20) return true;"
    "var bodyTxt = document.body ? (document.body.innerText || '') : '';"
    "return bodyTxt.indexOf('X:') !== -1;"
)

PROBE_SI_META_JS: Final[str] = (
    "if (!window.si || typeof window.si.navigate !== 'function') return null;"
    "var maxG = Number(window.si.maxGalaxies"
    "  || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxGalaxies) || 0);"
    "var maxS = Number(window.si.maxSystem"
    "  || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxSystem) || 0);"
    "return {ok: true, maxG: maxG, maxS: maxS};"
)

WAIT_GALAXY_STEP_LANDED_JS: Final[str] = (
    "var pvErr = document.getElementById('pageview-error');"
    "var pvAmt = document.getElementById('pageview-amount');"
    "if (pvErr && pvErr.style && pvErr.style.display !== 'none' && pvAmt && (pvAmt.textContent || '').trim()) {"
    "  return {throttled: true};"
    "}"
    "if (!window.si) return {missing: true};"
    "var trk = document.getElementById('g2w-track');"
    "var wideAnim = Boolean(trk && trk.style && trk.style.transform"
    "  && trk.style.transform !== 'none' && trk.style.transform !== 'translate3d(0px, 0px, 0px)');"
    "var swapAnim = Boolean((document.body && document.body.classList"
    "  && document.body.classList.contains('system-changing')) || window.si._hardNavPending || wideAnim);"
    "return {"
    "  busy: Boolean(window.si._getStatus),"
    "  animating: swapAnim,"
    "  g: Number(window.si.galaxy || 0),"
    "  s: Number(window.si.sun || 0),"
    "  resG: window.si.result && window.si.result.position ? Number(window.si.result.position.galaxy || 0) : 0,"
    "  resS: window.si.result && window.si.result.position"
    "    ? Number(window.si.result.position.system || window.si.result.position.sun || 0) : 0"
    "};"
)

_ARROW_VIS_HELPER_JS: Final[str] = (
    "function isVis(el) {"
    "  if (!el) return false;"
    "  var r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;"
    "  var hasBox = (el.offsetWidth > 0 && el.offsetHeight > 0) || (r && r.width > 0 && r.height > 0);"
    "  if (!hasBox) return false;"
    "  var cur = el;"
    "  while (cur && cur.nodeType === 1) {"
    "    var cs = window.getComputedStyle ? window.getComputedStyle(cur) : null;"
    "    if (cs && (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0')) return false;"
    "    cur = cur.parentElement;"
    "  }"
    "  return true;"
    "}"
)


def _build_arrow_click_js(axis_key: str, fn_name: str) -> str:
    return (
        "var delta = Number(arguments[0]);"
        "var returnEl = Boolean(arguments[1]);"
        "var sels = delta > 0"
        f"  ? ['[onclick*=\"g2w.nav(\\'{axis_key}\\',1)\"]', '[onclick*=\"g2w.nav(\\'{axis_key}\\', 1)\"]',"
        f"     'img[onclick*=\"{fn_name}(1)\"]', '[onclick*=\"{fn_name}(1)\"]',"
        f"     'img[onclick*=\"{fn_name}(+1)\"]', '[onclick*=\"{fn_name}( 1)\"]']"
        f"  : ['[onclick*=\"g2w.nav(\\'{axis_key}\\',-1)\"]', '[onclick*=\"g2w.nav(\\'{axis_key}\\', -1)\"]',"
        f"     'img[onclick*=\"{fn_name}(-1)\"]', '[onclick*=\"{fn_name}(-1)\"]',"
        f"     '[onclick*=\"{fn_name}( -1)\"]'];" + _ARROW_VIS_HELPER_JS + "var cands = [];"
        "if (document.querySelectorAll) {"
        "  for (var si = 0; si < sels.length; si++) {"
        "    try {"
        "      var list = document.querySelectorAll(sels[si]);"
        "      for (var li = 0; li < list.length; li++) {"
        "        if (cands.indexOf(list[li]) === -1) cands.push(list[li]);"
        "      }"
        "    } catch (e) {}"
        "  }"
        "  if (!cands.length) {"
        "    var rx = delta > 0"
        f"      ? /(?:g2w\\.nav\\(\\s*['\"]{axis_key}['\"]\\s*,\\s*\\+?1\\s*\\)|{fn_name}\\(\\s*\\+?1\\s*\\))/"
        f"      : /(?:g2w\\.nav\\(\\s*['\"]{axis_key}['\"]\\s*,\\s*-1\\s*\\)|{fn_name}\\(\\s*-1\\s*\\))/;"
        "    var allOc = document.querySelectorAll('[onclick]');"
        "    for (var ai = 0; ai < allOc.length; ai++) {"
        "      var oc = String(allOc[ai].getAttribute('onclick') || '');"
        "      if (rx.test(oc)) cands.push(allOc[ai]);"
        "    }"
        "  }"
        "}"
        "var chosen = null;"
        "for (var i = 0; i < cands.length; i++) { if (isVis(cands[i])) { chosen = cands[i]; break; } }"
        "if (!chosen && cands.length > 0) chosen = cands[0];"
        "if (chosen) {"
        "  if (returnEl) return {clicked: 'element', el: chosen};"
        "  if (typeof chosen.click === 'function') { chosen.click(); return {clicked: 'dom'}; }"
        "}"
        f"if (window.si && typeof window.si.{fn_name} === 'function') {{"
        f"  window.si.{fn_name}(delta); return {{clicked: 'fn'}};"
        "}"
        "return {clicked: 'none'};"
    )


CLICK_SYSTEM_ARROW_JS: Final[str] = _build_arrow_click_js("sys", "navigateSystem")
FALLBACK_SYSTEM_ARROW_JS: Final[str] = (
    "if (window.si && typeof window.si.navigateSystem === 'function') {"
    "  window.si.navigateSystem(Number(arguments[0])); return true;"
    "} return false;"
)

CLICK_GALAXY_ARROW_JS: Final[str] = _build_arrow_click_js("gal", "navigateGalaxy")
FALLBACK_GALAXY_ARROW_JS: Final[str] = (
    "if (window.si && typeof window.si.navigateGalaxy === 'function') {"
    "  window.si.navigateGalaxy(Number(arguments[0])); return true;"
    "} return false;"
)

PROBE_STEP_STATE_JS: Final[str] = (
    "var cg = document.getElementById('si-captcha-gate');"
    "if (cg && window.getComputedStyle(cg).display !== 'none'"
    "  && window.getComputedStyle(cg).visibility !== 'hidden'"
    "  && cg.offsetWidth > 0 && cg.offsetHeight > 0) return 'captcha';"
    "if (window.si && typeof window.si.navigate === 'function' && typeof window.si.getData === 'function') {"
    "  var resPos = (window.si.result && window.si.result.position) ? window.si.result.position : null;"
    "  var resG = Number((resPos && resPos.galaxy) || 0);"
    "  var resS = Number((resPos && (resPos.system || resPos.sun)) || 0);"
    "  if (!window.si._getStatus && resG > 0 && resS > 0) {"
    "    if (Number(window.si.galaxy || 0) !== resG) window.si.galaxy = resG;"
    "    if (Number(window.si.sun || 0) !== resS) window.si.sun = resS;"
    "  }"
    "  var cgVal = resG || Number(window.si.galaxy || 0);"
    "  var csVal = resS || Number(window.si.sun || 0);"
    "  var maxS = Number(window.si.maxSystem"
    "    || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxSystem) || 400);"
    "  if (!maxS || maxS < 1) maxS = 400;"
    "  var maxG = Number(window.si.maxGalaxies"
    "    || (window.SI_TPL && window.SI_TPL.galaxy && window.SI_TPL.galaxy.maxGalaxies) || 0);"
    "  if (!maxG || maxG < 1) maxG = 0;"
    "  return {ok: true, g: cgVal, s: csVal, maxS: maxS, maxG: maxG, busy: Boolean(window.si._getStatus)};"
    "}"
    "return null;"
)

POLL_BUSY_STATE_JS: Final[str] = (
    "if (!window.si) return null;"
    "var rp = (window.si.result && window.si.result.position) ? window.si.result.position : null;"
    "var rg = Number((rp && rp.galaxy) || 0);"
    "var rs = Number((rp && (rp.system || rp.sun)) || 0);"
    "if (!window.si._getStatus && rg > 0 && rs > 0) {"
    "  if (Number(window.si.galaxy || 0) !== rg) window.si.galaxy = rg;"
    "  if (Number(window.si.sun || 0) !== rs) window.si.sun = rs;"
    "}"
    "return {busy: Boolean(window.si._getStatus), g: rg || Number(window.si.galaxy || 0),"
    "  s: rs || Number(window.si.sun || 0)};"
)

TRIGGER_SI_NAV_JS: Final[str] = (
    "window.__siSweepDone = false;"
    "var targetG = Number(arguments[0]), targetS = Number(arguments[1]);"
    "if (Number(window.si.galaxy) === targetG && Number(window.si.sun) === targetS"
    "  && !window.si._getStatus && window.si.result) {"
    "  window.si.getData();"
    "} else {"
    "  window.si.navigate(targetG, targetS);"
    "}"
)

CHECK_SI_GETDATA_JS: Final[str] = "return Boolean(window.si && typeof window.si.getData === 'function');"

DISPATCH_SPY_JS: Final[str] = (
    "var g = document.getElementById('fa-gal'); if (g) g.value = String(arguments[0]);"
    "var s = document.getElementById('fa-sun'); if (s) s.value = String(arguments[1]);"
    "var p = document.getElementById('fa-pla'); if (p) p.value = String(arguments[2]);"
    "var f = document.getElementById('fleet-spy'); if (f) f.value = String(arguments[3]);"
    "var mf = document.getElementById('message-fleetstart'); if (mf) mf.style.display = 'none';"
    "var mf2 = document.getElementById('message-fleetstart-2'); if (mf2) mf2.style.display = 'none';"
    "var me = document.getElementById('message-error'); if (me) me.style.display = 'none';"
    "var met = document.getElementById('message-error-text'); if (met) met.textContent = '';"
    "if (window.si && window.si.result) { delete window.si.result.flightStarted; delete window.si.result.err; }"
    "window.si.getData(arguments[4]);"
)

POLL_SPY_OUTCOME_JS: Final[str] = (
    "var pvErr = document.getElementById('pageview-error');"
    "var pvAmt = document.getElementById('pageview-amount');"
    "if (pvErr && pvErr.style && pvErr.style.display !== 'none' && pvAmt && (pvAmt.textContent || '').trim()) {"
    "  return {throttled: true};"
    "}"
    "if (!window.si) return {done: true, err: 'window.si lost'};"
    "if (window.si._getStatus) return {busy: true};"
    "var r = window.si.result || {};"
    "var errEl = document.getElementById('message-error-text') || document.getElementById('message-error-text-mobile');"
    "var domErr = errEl ? (errEl.textContent || '').trim() : '';"
    "return {"
    "  busy: false,"
    "  flightStarted: r.flightStarted !== undefined ? r.flightStarted : null,"
    "  err: r.err !== undefined ? String(r.err || '') : domErr"
    "};"
)
