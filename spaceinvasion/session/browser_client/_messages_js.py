"""JavaScript snippets executed inside `F_Hauptframe` for SpaceInvasion inbox operations."""

from __future__ import annotations

from typing import Final

CLICK_COLLAPSED_TRIGGERS_JS: Final[str] = """
var triggers = document.querySelectorAll(
    '[onclick*="showMessage"], [onclick*="openMessage"], ' +
    '[onclick*="toggleMessage"], [onclick*="readMessage"], ' +
    '[onclick*="loadMessage"], .msg-header:not(.open), .message-header:not(.open)'
);
var count = 0;
for (var i = 0; i < triggers.length && count < 25; i++) {
    var el = triggers[i];
    if (el.tagName === 'INPUT' || el.tagName === 'SELECT') continue;
    var oc = (el.getAttribute('onclick') || '').toLowerCase();
    if (oc.indexOf('del') !== -1) continue;
    try { el.click(); count++; } catch (e) {}
}
return count;
"""

EXPAND_MESSAGES_ASYNC_JS: Final[str] = r"""
var sidQuery = String(arguments[0] || '');
var maxFetches = Number(arguments[1] || 12);
var done = arguments[arguments.length - 1];
var tasks = [];
var rows = Array.prototype.slice.call(document.querySelectorAll('tr'));

for (var i = 0; i < rows.length; i++) {
    var tr = rows[i];
    if (tr.getAttribute('data-si-expanded') === '1') continue;
    if (tr.querySelector('.si-expanded-body')) {
        tr.setAttribute('data-si-expanded', '1');
        continue;
    }
    var spioEl = tr.querySelector('[onclick*="showSpionageReport"]');
    if (spioEl) {
        var oc = String(spioEl.getAttribute('onclick') || '');
        var mSp = oc.match(/showSpionageReport\s*\(\s*['"]?(\d+)['"]?/i);
        if (mSp) {
            tasks.push({
                tr: tr, kind: 'spio', prio: 0,
                url: 'indexInternal.es?action=internalSpionageReport&reportID=' + mSp[1] + (sidQuery ? '&' + sidQuery : '')
            });
            continue;
        }
    }
    var bsumCell = tr.querySelector('[data-bsum]') || (tr.getAttribute('data-bsum') ? tr : null);
    var bsumOc = tr.querySelector('[onclick*="showBattleReport"]');
    var bid = '';
    if (bsumCell) bid = String(bsumCell.getAttribute('data-bsum') || '').trim();
    if (!bid && bsumOc) {
        var mBt = String(bsumOc.getAttribute('onclick') || '').match(/showBattleReport\s*\(\s*['"]?(\d+)['"]?/i);
        if (mBt) bid = mBt[1];
    }
    if (bid && /^\d+$/.test(bid)) {
        tasks.push({
            tr: tr, kind: 'battle', prio: 0, bid: bid,
            url: '/indexGame.es?battle=' + bid + (sidQuery ? '&' + sidQuery : '')
        });
        continue;
    }
    var readLink = tr.querySelector('a[href*="action=internalMessages"][href*="read="]');
    if (readLink) {
        var href = String(readLink.getAttribute('href') || '').trim();
        var rtxt = String(tr.textContent || '').toLowerCase();
        var isRoutineReturn = (
            (rtxt.indexOf('fleet return') !== -1 || rtxt.indexOf('zurückgekehrt') !== -1 || rtxt.indexOf('zurueckgekehrt') !== -1) &&
            rtxt.indexOf('recycl') === -1 && rtxt.indexOf('rubble') === -1 &&
            rtxt.indexOf('coloniz') === -1 && rtxt.indexOf('transport') === -1
        );
        if (href) {
            tasks.push({tr: tr, kind: 'read', prio: isRoutineReturn ? 1 : 0, url: href});
        }
    }
}

tasks.sort(function(a, b) { return a.prio - b.prio; });
if (tasks.length === 0 || typeof window.fetch !== 'function') {
    done({expanded: 0, fetches: 0});
    return;
}

var idx = 0, expCount = 0, fetchCount = 0;
var resNames = {1: 'Pig iron', 2: 'Metal', 3: 'Kryptonite', 4: 'Spice'};

function isRateLimitedHtml(raw) {
    var low = String(raw || '').toLowerCase();
    if (low.indexOf('made more than') === -1 || low.indexOf('page views') === -1) return false;
    if (/id=["']pageview-amount["'][^>]*>\s*<\/span>/i.test(raw)) return false;
    return true;
}

function attachDiv(tr, text, extraAttrName, extraAttrVal) {
    var cells = tr.querySelectorAll('td');
    var host = cells.length > 0 ? cells[cells.length - 1] : tr;
    var div = document.createElement('div');
    div.className = 'si-expanded-body';
    div.setAttribute('data-si-expanded', '1');
    div.style.display = 'none';
    div.textContent = ' ' + text + ' ';
    host.appendChild(div);
    if (extraAttrName && extraAttrVal) tr.setAttribute(extraAttrName, extraAttrVal);
    tr.setAttribute('data-si-expanded', '1');
}

function step() {
    if (idx >= tasks.length || fetchCount >= maxFetches) {
        done({expanded: expCount, fetches: fetchCount});
        return;
    }
    var t = tasks[idx++];
    fetchCount++;
    window.fetch(t.url, {credentials: 'same-origin'})
        .then(function(resp) { return resp.text(); })
        .then(function(raw) {
            if (!raw || isRateLimitedHtml(raw)) { fetchCount = maxFetches; return; }
            if (t.kind === 'battle') {
                try {
                    var bj = JSON.parse(raw);
                    if (bj && bj.ok) {
                        var parts = ['Battle report #' + t.bid];
                        if (bj.rounds !== undefined) parts.push('Rounds: ' + bj.rounds);
                        if (bj.winner !== undefined) parts.push('Winner: ' + bj.winner);
                        if (bj.attacker && bj.attacker.name) parts.push('Attacker: ' + bj.attacker.name + ' (Lost: ' + (bj.attacker.lost || 0) + ')');
                        if (bj.defender && bj.defender.name) parts.push('Defender: ' + bj.defender.name + ' (Lost: ' + (bj.defender.lost || 0) + ')');
                        if (Array.isArray(bj.stolen) && bj.stolen.length > 0) {
                            var sp = [];
                            for (var s = 0; s < bj.stolen.length; s++) {
                                var si = bj.stolen[s] || {};
                                sp.push((resNames[si.r] || si.n || ('R' + si.r)) + ': ' + (si.v || 0));
                            }
                            parts.push('Plundered ' + sp.join(' '));
                        }
                        if (Array.isArray(bj.rubble) && bj.rubble.length > 0) {
                            var rp = [];
                            for (var r = 0; r < bj.rubble.length; r++) {
                                var ri = bj.rubble[r] || {};
                                rp.push((resNames[ri.r] || ri.n || ('R' + ri.r)) + ': ' + (ri.v || 0));
                            }
                            parts.push('Debris field ' + rp.join(' '));
                        }
                        attachDiv(t.tr, parts.join(' | '), 'data-battle-json', raw.slice(0, 8000));
                        expCount++;
                    }
                } catch (e) {}
                return;
            }
            var parser = new DOMParser();
            var doc = parser.parseFromString(raw, 'text/html');
            var junk = doc.querySelectorAll(
                'script, style, #planet-coords, #planet-list, form#ress, #ress, ' +
                '#roheisen, #metall, #kryptonit, #spice, #tutorial, .tutorial, #speedup, .speedup, ' +
                'select[name="planet"], .messagebox, div[id^="msg-"], form[name="messages"]'
            );
            for (var j = 0; j < junk.length; j++) {
                if (t.kind === 'read' && junk[j].tagName === 'FORM') continue;
                if (junk[j].parentNode) junk[j].parentNode.removeChild(junk[j]);
            }
            var bodyEl = doc.body || doc.documentElement;
            if (t.kind === 'read') {
                var formEl = doc.querySelector('form[name="messages"]');
                if (formEl) {
                    var cbs = formEl.querySelectorAll('input[type="checkbox"], select[name="todo"]');
                    for (var k = 0; k < cbs.length; k++) {
                        var rowP = cbs[k].closest ? cbs[k].closest('tr') : null;
                        if (rowP && rowP.parentNode && !rowP.querySelector('.nachricht')) {
                            rowP.parentNode.removeChild(rowP);
                        }
                    }
                }
            }
            var cleanTxt = String((bodyEl && bodyEl.textContent) || '').replace(/\s+/g, ' ').trim();
            if (t.kind === 'read' && cleanTxt) {
                var mInner = cleanTxt.match(/(?:Message\s+from|Nachricht\s+von)\s+[^:]{1,60}:[\s\S]*?(?=\s*['"]?\s*(?:Fleet\s+reports|Battle\s+reports|Espionage|System|User|Alliance)\s*[\u2605*]|\s*[\u2605*]\s*Favourites|\s*Received\s+on:\s*From:|\s*Empfangen\s+am:|$)/i);
                if (mInner && mInner[0] && mInner[0].trim().length > 4) cleanTxt = mInner[0].trim();
            }
            if (cleanTxt.length > 4) { attachDiv(t.tr, cleanTxt.slice(0, 6000), '', ''); expCount++; }
        })
        .catch(function() {})
        .then(function() {
            if (idx < tasks.length && fetchCount < maxFetches) setTimeout(step, 140);
            else done({expanded: expCount, fetches: fetchCount});
        });
}
step();
"""

_MSG_CB_HELPERS_JS: Final[str] = """
var targetIds = arguments[0] || [];
var allowAll = Boolean(arguments[1]);
var idSet = {};
for (var i = 0; i < targetIds.length; i++) idSet[String(targetIds[i])] = true;

function isSelectAll(cb) {
    var nm = String(cb.name || '').trim().toLowerCase();
    var cid = String(cb.id || '').trim().toLowerCase();
    if (nm === 'check' || cid === 'check') return true;
    var blob = [nm, cid, cb.className || '', cb.getAttribute('onclick') || '', cb.getAttribute('onchange') || ''].join(' ').toLowerCase();
    return (
        blob.indexOf('allmessages') !== -1 || blob.indexOf('checkall') !== -1 ||
        blob.indexOf('selectall') !== -1 || blob.indexOf('markall') !== -1 ||
        blob.indexOf('toggleall') !== -1 || blob.indexOf('setall') !== -1
    );
}

function matchCbId(cb) {
    var v = String(cb.value || '').trim();
    if (v && idSet[v]) return true;
    var nm = String(cb.name || '');
    var cid = String(cb.id || '');
    for (var k in idSet) {
        if (!Object.prototype.hasOwnProperty.call(idSet, k)) continue;
        if (
            nm.indexOf('[' + k + ']') !== -1 || nm === 'check_' + k || nm === 'check-' + k ||
            nm === 'del_' + k || nm === 'msg_' + k ||
            cid === 'check_' + k || cid === 'msg_' + k || cid === 'del_' + k || cid === k
        ) return true;
    }
    var row = cb.closest ? cb.closest('tr, div.message, div.msg, [data-msg-id], [data-id]') : null;
    if (row) {
        var dm = String(row.getAttribute('data-msg-id') || row.getAttribute('data-id') || row.getAttribute('data-bsum') || '');
        var rid = String(row.id || '').replace(/^(?:msg|message|row|tr_msg)[_-]?/i, '');
        if ((dm && idSet[dm]) || (rid && idSet[rid])) return true;
        var mfav = row.querySelector ? row.querySelector('[data-mfav], [data-bsum]') : null;
        if (mfav) {
            var mf = String(mfav.getAttribute('data-mfav') || mfav.getAttribute('data-bsum') || '');
            if (mf && idSet[mf]) return true;
        }
    }
    return false;
}
"""

PROBE_DELETE_MODE_JS: Final[str] = (
    _MSG_CB_HELPERS_JS
    + """
var cbs = Array.prototype.slice.call(document.querySelectorAll('input[type="checkbox"]'));
var msgCbs = cbs.filter(function(cb) { return !isSelectAll(cb); });
var matchedCbs = msgCbs.filter(matchCbId);
if (matchedCbs.length === 0 && allowAll && msgCbs.length > 0) matchedCbs = msgCbs;
if (matchedCbs.length > 0) {
    var form = matchedCbs[0].form || document.querySelector('form[action*="internalMessages"], form');
    if (form) return 'checkbox_form';
}
var links = Array.prototype.slice.call(
    document.querySelectorAll('a[href*="del"], a[onclick*="del"], button[onclick*="del"], input[onclick*="del"]')
);
for (var j = 0; j < links.length; j++) {
    var h = String(links[j].getAttribute('href') || '') + ' ' + String(links[j].getAttribute('onclick') || '');
    for (var idKey in idSet) {
        if (Object.prototype.hasOwnProperty.call(idSet, idKey) && h.indexOf(idKey) !== -1) return 'direct_link';
    }
}
if (allowAll) {
    for (var m = 0; m < links.length; m++) {
        var hl = (String(links[m].getAttribute('href') || '') + ' ' + String(links[m].textContent || '')).toLowerCase();
        if (hl.indexOf('delete all') !== -1 || hl.indexOf('alle l') !== -1 || hl.indexOf('del=all') !== -1 || hl.indexOf('deleteall') !== -1) {
            return 'delete_all_link';
        }
    }
}
return '';
"""
)

EXEC_DELETE_JS: Final[str] = (
    _MSG_CB_HELPERS_JS
    + """
var mode = String(arguments[2] || '');
if (mode === 'checkbox_form') {
    var cbs = Array.prototype.slice.call(document.querySelectorAll('input[type="checkbox"]'));
    var msgCbs = cbs.filter(function(cb) { return !isSelectAll(cb); });
    var matched = msgCbs.filter(matchCbId);
    if (matched.length === 0 && allowAll) matched = msgCbs;
    if (matched.length === 0) return false;
    for (var c = 0; c < matched.length; c++) matched[c].checked = true;
    var form = matched[0].form || document.querySelector('form[action*="internalMessages"], form');
    if (!form) return false;
    var selects = form.querySelectorAll('select');
    for (var s = 0; s < selects.length; s++) {
        var sel = selects[s];
        if ((sel.name || '').toLowerCase() === 'planet') continue;
        var pickedIdx = -1;
        for (var o = 0; o < sel.options.length; o++) {
            var rawVal = String(sel.options[o].value || '').trim().toLowerCase();
            var ov = (rawVal + ' ' + String(sel.options[o].text || '')).toLowerCase();
            if (rawVal === 'deleted') { pickedIdx = o; break; }
            if ((ov.indexOf('marked') !== -1 || ov.indexOf('selected') !== -1 || ov.indexOf('markiert') !== -1 || ov.indexOf('ausgew') !== -1) &&
                (ov.indexOf('del') !== -1 || ov.indexOf('lösch') !== -1 || ov.indexOf('loesch') !== -1 || ov.indexOf('remove') !== -1)) {
                pickedIdx = o; break;
            }
        }
        if (pickedIdx < 0) {
            for (var o2 = 0; o2 < sel.options.length; o2++) {
                var ov2 = (String(sel.options[o2].value || '').trim() + ' ' + String(sel.options[o2].text || '')).toLowerCase();
                if ((ov2.indexOf('del') !== -1 || ov2.indexOf('lösch') !== -1 || ov2.indexOf('loesch') !== -1) &&
                    ov2.indexOf('all') === -1 && ov2.indexOf('alle') === -1) { pickedIdx = o2; break; }
            }
        }
        if (pickedIdx < 0) {
            for (var o3 = 0; o3 < sel.options.length; o3++) {
                var ov3 = (sel.options[o3].value + ' ' + sel.options[o3].text).toLowerCase();
                if (ov3.indexOf('marked') !== -1 || ov3.indexOf('selected') !== -1 || ov3.indexOf('markiert') !== -1 || ov3.indexOf('ausgew') !== -1) {
                    pickedIdx = o3; break;
                }
            }
        }
        if (pickedIdx >= 0) sel.selectedIndex = pickedIdx;
    }
    var cands = Array.prototype.slice.call(form.querySelectorAll('input[type="submit"], button[type="submit"], input[type="button"], button'));
    var delBtn = null;
    for (var b = 0; b < cands.length; b++) {
        var bt = cands[b];
        var btxt = [bt.name || '', bt.id || '', bt.value || '', bt.getAttribute('onclick') || '', bt.textContent || ''].join(' ').toLowerCase();
        if (btxt.indexOf('del') !== -1 || btxt.indexOf('lösch') !== -1 || btxt.indexOf('loesch') !== -1 || btxt.indexOf('remove') !== -1 || btxt.indexOf('trash') !== -1) {
            delBtn = bt; break;
        }
    }
    if (delBtn) { delBtn.click(); return true; }
    var nonReadSubmits = cands.filter(function(bt) {
        if ((bt.type || '').toLowerCase() !== 'submit') return false;
        var btxt = [bt.name || '', bt.value || '', bt.textContent || ''].join(' ').toLowerCase();
        return btxt.indexOf('read') === -1 && btxt.indexOf('gelesen') === -1 && btxt.indexOf('ungelesen') === -1;
    });
    if (nonReadSubmits.length > 0) { nonReadSubmits[0].click(); return true; }
    form.submit();
    return true;
}
var links = Array.prototype.slice.call(
    document.querySelectorAll('a[href*="del"], a[onclick*="del"], button[onclick*="del"], input[onclick*="del"]')
);
if (mode === 'direct_link') {
    for (var j = 0; j < links.length; j++) {
        var h = String(links[j].getAttribute('href') || '') + ' ' + String(links[j].getAttribute('onclick') || '');
        for (var idKey in idSet) {
            if (Object.prototype.hasOwnProperty.call(idSet, idKey) && h.indexOf(idKey) !== -1) { links[j].click(); return true; }
        }
    }
}
if (mode === 'delete_all_link' && allowAll) {
    for (var m = 0; m < links.length; m++) {
        var hl = (String(links[m].getAttribute('href') || '') + ' ' + String(links[m].textContent || '')).toLowerCase();
        if (hl.indexOf('delete all') !== -1 || hl.indexOf('alle l') !== -1 || hl.indexOf('del=all') !== -1 || hl.indexOf('deleteall') !== -1) {
            links[m].click(); return true;
        }
    }
}
return false;
"""
)
