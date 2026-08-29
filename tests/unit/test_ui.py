"""Unit and Node-VM integration tests for the Operator Control UI (F16 & F21)."""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

from spaceinvasion.api_server import GameApiBridge, create_api_server
from spaceinvasion.state import (
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
)
from spaceinvasion.strategy import FUSION_PLANT, PIG_IRON_MINE


def _sample_state() -> GameState:
    offers = (
        BuildingOffer(
            building_id=FUSION_PLANT,
            name="Fusion plant",
            current_level=3,
            target_level=4,
            cost=Resources(276, 0, 121, 0),
            can_build=True,
            status_text="Build level 4",
            build_href="indexInternal.es?action=internalBuildItem&buildingID=5#5",
        ),
        BuildingOffer(
            building_id=PIG_IRON_MINE,
            name="Pig iron mine",
            current_level=3,
            target_level=4,
            cost=Resources(269, 0, 87, 0),
            can_build=True,
            status_text="Build level 4",
            build_href="indexInternal.es?action=internalBuildItem&buildingID=1#1",
        ),
    )
    planet = PlanetState(
        planet_id=12476,
        name="Colony",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=9,
        fields_max=230,
        resources=Resources(5500, 900, 3100, 200),
        storage=Resources(100000, 100000, 100000, 100000),
        energy=EnergyBalance(45, 38),
        buildings={"b1": 3, "b3": 3, "b5": 3},
        building_names={"b1": "Pig iron mine", "b3": "Kryptonite mine", "b5": "Fusion plant"},
        building_offers=offers,
    )
    return GameState(
        universe_id=31,
        user_id=103603,
        nickname="ventkx86",
        timestamp=1234.5,
        planets=(planet,),
        active_planet_id=12476,
        researches={"r1": 0},
        research_names={"r1": "Mining technology"},
        speedup_factor=10.0,
    )


class _TableInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict] = []
        self._cur_table: dict | None = None
        self._in_thead = False
        self._in_th = False
        self._th_buf: list[str] = []
        self._in_tbody = False
        self._cur_tbody_id = ""
        self._cur_tr_colspans: list[int] = []
        self._in_planner_select = False
        self.planner_options: list[str] = []
        self.has_planner_select = False
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        amap = dict(attrs)
        if amap.get("id"):
            self.ids.add(str(amap["id"]))
        if tag == "select" and amap.get("id") == "cfg-planner_mode":
            self.has_planner_select = True
            self._in_planner_select = True
        elif tag == "option" and self._in_planner_select and amap.get("value") is not None:
            self.planner_options.append(str(amap["value"]))
        elif tag == "table":
            self._cur_table = {"headers": [], "tbody_id": "", "placeholder_colspans": []}
        elif self._cur_table is not None:
            if tag == "thead":
                self._in_thead = True
            elif tag == "th" and self._in_thead:
                self._in_th = True
                self._th_buf = []
            elif tag == "tbody":
                self._in_tbody = True
                self._cur_tbody_id = str(amap.get("id") or "")
                self._cur_table["tbody_id"] = self._cur_tbody_id
            elif tag == "tr" and self._in_tbody:
                self._cur_tr_colspans = []
            elif tag == "td" and self._in_tbody:
                cs = amap.get("colspan")
                if cs and cs.isdigit():
                    self._cur_tr_colspans.append(int(cs))

    def handle_data(self, data: str) -> None:
        if self._in_th:
            self._th_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._in_planner_select:
            self._in_planner_select = False
        elif self._cur_table is not None:
            if tag == "th" and self._in_th:
                self._cur_table["headers"].append("".join(self._th_buf).strip())
                self._in_th = False
            elif tag == "thead":
                self._in_thead = False
            elif tag == "tr" and self._in_tbody and self._cur_tr_colspans:
                self._cur_table["placeholder_colspans"].extend(self._cur_tr_colspans)
                self._cur_tr_colspans = []
            elif tag == "tbody":
                self._in_tbody = False
            elif tag == "table":
                self.tables.append(self._cur_table)
                self._cur_table = None


_NODE_DOM_HARNESS = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const html = fs.readFileSync(cfg.htmlPath, "utf8");
const appJs = fs.readFileSync(cfg.appJsPath, "utf8");
const scenarioJs = fs.readFileSync(cfg.scenarioPath, "utf8");

function parseAttrs(attrStr) {
  const attrs = {};
  const re = /([A-Za-z_:][A-Za-z0-9_.:-]*)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let m;
  while ((m = re.exec(attrStr)) !== null) {
    const key = m[1];
    const val = m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : ""));
    attrs[key] = val;
  }
  return attrs;
}

function makeStyleProxy() {
  const props = new Map();
  return new Proxy({
    setProperty(k, v) { props.set(String(k), String(v)); },
    getPropertyValue(k) { return props.get(String(k)) || ""; },
    removeProperty(k) { props.delete(String(k)); },
  }, {
    get(target, prop) {
      if (prop in target) return target[prop];
      return props.get(String(prop)) || "";
    },
    set(_target, prop, val) {
      props.set(String(prop), String(val));
      return true;
    },
  });
}

class MiniElement {
  constructor(tagName, attrs = {}) {
    this.nodeType = 1;
    this.tagName = String(tagName || "DIV").toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.parentElement = null;
    this._attrs = {};
    this._listeners = new Map();
    this.style = makeStyleProxy();
    this.dataset = {};
    this._text = "";
    this._id = "";
    this._classSet = new Set();
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.selected = false;
    this.colSpan = 1;
    this.value = "";
    this.type = "";
    this.href = "";
    this.min = "";
    this.max = "";
    this.step = "";
    const self = this;
    this.classList = {
      add(...cs) { cs.forEach((c) => { if (c) self._classSet.add(c); }); },
      remove(...cs) { cs.forEach((c) => self._classSet.delete(c)); },
      contains(c) { return self._classSet.has(c); },
      toggle(c, force) {
        if (force === undefined) {
          if (self._classSet.has(c)) { self._classSet.delete(c); return false; }
          self._classSet.add(c); return true;
        }
        if (force) { self._classSet.add(c); return true; }
        self._classSet.delete(c); return false;
      },
    };
    for (const [k, v] of Object.entries(attrs)) this.setAttribute(k, v);
  }
  get id() { return this._id; }
  set id(v) { this._id = String(v || ""); this._attrs.id = this._id; }
  get className() { return Array.from(this._classSet).join(" "); }
  set className(v) {
    this._classSet = new Set(String(v || "").split(/\s+/).filter(Boolean));
    this._attrs.class = this.className;
  }
  get textContent() {
    if (this.children.length === 0) return this._text;
    return this._text + this.children.map((c) => c.textContent).join(" ");
  }
  set textContent(v) {
    this.children = [];
    this._text = v == null ? "" : String(v);
  }
  setAttribute(k, v) {
    const key = String(k);
    const val = v == null ? "" : String(v);
    this._attrs[key] = val;
    if (key === "id") this._id = val;
    else if (key === "class") this.className = val;
    else if (key === "hidden") this.hidden = true;
    else if (key === "disabled") this.disabled = true;
    else if (key === "checked") this.checked = true;
    else if (key === "selected") this.selected = true;
    else if (key === "value") this.value = val;
    else if (key === "type") this.type = val;
    else if (key === "href") this.href = val;
    else if (key.startsWith("data-")) {
      const camel = key.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      this.dataset[camel] = val;
    }
  }
  getAttribute(k) {
    if (k === "id") return this._id || null;
    if (k === "class") return this.className || null;
    return k in this._attrs ? this._attrs[k] : null;
  }
  removeAttribute(k) {
    delete this._attrs[k];
    if (k === "hidden") this.hidden = false;
    if (k === "disabled") this.disabled = false;
  }
  appendChild(child) {
    if (!child) return child;
    if (child.nodeType === 3) {
      this._text += child.textContent;
      return child;
    }
    child.parentNode = this;
    child.parentElement = this;
    this.children.push(child);
    if (this.tagName === "SELECT" && child.tagName === "OPTION" && (child.selected || this.value === "")) {
      this.value = child.value;
    }
    return child;
  }
  append(...nodes) {
    for (const n of nodes) {
      if (typeof n === "string") this._text += n;
      else this.appendChild(n);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this._text = "";
    this.append(...nodes);
  }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    const arr = this._listeners.get(type) || [];
    this._listeners.set(type, arr.filter((x) => x !== fn));
  }
  dispatchEvent(evOrType) {
    const ev = typeof evOrType === "string" ? { type: evOrType, target: this, currentTarget: this, stopPropagation() {}, preventDefault() {} } : { ...evOrType, target: evOrType.target || this, currentTarget: this, stopPropagation() {}, preventDefault() {} };
    for (const fn of (this._listeners.get(ev.type) || [])) {
      fn.call(this, ev);
    }
    return true;
  }
  click() {
    if (typeof this.onclick === "function") this.onclick({ type: "click", target: this, currentTarget: this, stopPropagation() {}, preventDefault() {} });
    this.dispatchEvent("click");
  }
  matches(sel) {
    return sel.split(",").map((s) => s.trim()).filter(Boolean).some((part) => {
      const tokens = part.trim().split(/\s+/);
      if (tokens.length === 1) return matchAtom(this, tokens[0]);
      if (!matchAtom(this, tokens[tokens.length - 1])) return false;
      let cur = this.parentElement;
      for (let i = tokens.length - 2; i >= 0; i--) {
        while (cur && !matchAtom(cur, tokens[i])) cur = cur.parentElement;
        if (!cur) return false;
        cur = cur.parentElement;
      }
      return true;
    });
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      for (const ch of node.children) {
        if (ch.matches(sel)) out.push(ch);
        walk(ch);
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
}

function matchAtom(el, atom) {
  let s = atom.trim();
  if (!s) return false;
  if (s === "*") return true;
  const tagM = /^[A-Za-z0-9_-]+/.exec(s);
  if (tagM) {
    if (el.tagName !== tagM[0].toUpperCase()) return false;
    s = s.slice(tagM[0].length);
  }
  while (s.length > 0) {
    if (s[0] === "#") {
      const m = /^#([A-Za-z0-9_-]+)/.exec(s);
      if (!m || el.id !== m[1]) return false;
      s = s.slice(m[0].length);
    } else if (s[0] === ".") {
      const m = /^\.([A-Za-z0-9_-]+)/.exec(s);
      if (!m || !el.classList.contains(m[1])) return false;
      s = s.slice(m[0].length);
    } else if (s[0] === "[") {
      const m = /^\[([^=\]]+)(?:="([^"]*)")?\]/.exec(s);
      if (!m) return false;
      const got = el.getAttribute(m[1]);
      if (m[2] !== undefined) { if (got !== m[2]) return false; }
      else if (got === null) return false;
      s = s.slice(m[0].length);
    } else {
      return false;
    }
  }
  return true;
}

const VOID_TAGS = new Set(["meta", "link", "input", "br", "hr", "img"]);
const root = new MiniElement("HTML");
const stack = [root];
const tokenRe = /<!--[\s\S]*?-->|<\/?([A-Za-z0-9_-]+)([^>]*)>|([^<]+)/g;
let tm;
while ((tm = tokenRe.exec(html)) !== null) {
  if (tm[3] !== undefined) {
    const txt = tm[3].replace(/\s+/g, " ").trim();
    if (txt) {
      const top = stack[stack.length - 1];
      top._text = top._text ? `${top._text} ${txt}` : txt;
    }
    continue;
  }
  if (!tm[1]) continue;
  const raw = tm[0];
  const tag = tm[1].toLowerCase();
  const isClose = raw.startsWith("</");
  if (isClose) {
    for (let i = stack.length - 1; i > 0; i--) {
      if (stack[i].tagName.toLowerCase() === tag) { stack.length = i; break; }
    }
    continue;
  }
  const attrs = parseAttrs(tm[2] || "");
  const node = new MiniElement(tag, attrs);
  stack[stack.length - 1].appendChild(node);
  const selfClose = raw.endsWith("/>") || VOID_TAGS.has(tag);
  if (!selfClose) stack.push(node);
}

const bodyEl = root.querySelector("body") || root;
const document = {
  visibilityState: "visible",
  documentElement: root,
  body: bodyEl,
  getElementById(id) {
    const walk = (n) => {
      if (n.id === id) return n;
      for (const ch of n.children) {
        const hit = walk(ch);
        if (hit) return hit;
      }
      return null;
    };
    return walk(root);
  },
  querySelectorAll(sel) { return root.querySelectorAll(sel); },
  querySelector(sel) { return root.querySelector(sel); },
  createElement(tag) { return new MiniElement(tag); },
  createElementNS(_ns, tag) { return new MiniElement(tag); },
  createTextNode(txt) { return { nodeType: 3, textContent: String(txt || "") }; },
  addEventListener() {},
};

const errors = [];
const recordedTimeoutDelays = [];
const fetchLog = [];
const timers = new Set();
const intervals = new Set();
const store = new Map();

const sandbox = {
  document,
  window: {
    location: { href: `${cfg.baseUrl}/`, port: String(new URL(cfg.baseUrl).port), hash: "", reload() {} },
    history: { replaceState() {} },
    addEventListener() {},
  },
  sessionStorage: {
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { store.delete(k); },
  },
  AbortController,
  URL,
  URLSearchParams,
  console: {
    log() {},
    warn() {},
    error(...args) { errors.push(args.map((a) => String(a)).join(" ")); },
  },
  setTimeout(fn, ms, ...args) {
    recordedTimeoutDelays.push(Number(ms || 0));
    const t = global.setTimeout(() => { timers.delete(t); fn(...args); }, Math.min(Number(ms || 0), 2500));
    timers.add(t);
    return t;
  },
  clearTimeout(t) { timers.delete(t); global.clearTimeout(t); },
  setInterval(fn, ms, ...args) {
    const t = global.setInterval(fn, Math.max(200, Number(ms || 0)), ...args);
    intervals.add(t);
    return t;
  },
  clearInterval(t) { intervals.delete(t); global.clearInterval(t); },
  fetch: async (url, opts = {}) => {
    const fullUrl = String(url).startsWith("http") ? String(url) : `${cfg.baseUrl}${url}`;
    const bodyObj = opts.body ? JSON.parse(String(opts.body)) : null;
    fetchLog.push({ url: String(url), method: opts.method || "GET", body: bodyObj });
    if (sandbox.__fetchHook) {
      const custom = await sandbox.__fetchHook(String(url), opts, fullUrl, global.fetch);
      if (custom !== undefined) return custom;
    }
    return await global.fetch(fullUrl, opts);
  },
  fetchLog,
  recordedTimeoutDelays,
  __waitUntil: async (pred, timeoutMs = 4000) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (pred()) return true;
      await new Promise((r) => global.setTimeout(r, 20));
    }
    return false;
  },
  __sleep: (ms) => new Promise((r) => global.setTimeout(r, ms)),
};

vm.createContext(sandbox);
if (cfg.fetchPatchJs) {
  sandbox.__fetchHook = vm.runInContext(`(${cfg.fetchPatchJs})`, sandbox);
}

vm.runInContext(appJs, sandbox);

(async () => {
  try {
    const scenario = await vm.runInContext(`(async () => { ${scenarioJs} })()`, sandbox);
    for (const t of timers) global.clearTimeout(t);
    for (const i of intervals) global.clearInterval(i);
    process.stdout.write(JSON.stringify({ ok: true, errors, recordedTimeoutDelays, fetchLog, scenario }));
  } catch (err) {
    for (const t of timers) global.clearTimeout(t);
    for (const i of intervals) global.clearInterval(i);
    process.stdout.write(JSON.stringify({ ok: false, error: String(err && err.stack ? err.stack : err), errors }));
    process.exitCode = 1;
  }
})();
"""


def _run_node_ui(base_url: str, scenario_js: str, *, fetch_patch_js: str = "") -> dict:
    ui_dir = Path(__file__).resolve().parents[2] / "spaceinvasion" / "ui"
    with tempfile.TemporaryDirectory(prefix="si_test_ui_") as tmp:
        tmp_path = Path(tmp)
        harness_js = tmp_path / "harness.cjs"
        scenario_path = tmp_path / "scenario.js"
        cfg_path = tmp_path / "cfg.json"
        harness_js.write_text(_NODE_DOM_HARNESS, encoding="utf-8")
        scenario_path.write_text(scenario_js, encoding="utf-8")
        cfg_path.write_text(
            json.dumps(
                {
                    "baseUrl": base_url,
                    "htmlPath": str(ui_dir / "index.html"),
                    "appJsPath": str(ui_dir / "app.js"),
                    "scenarioPath": str(scenario_path),
                    "fetchPatchJs": fetch_patch_js,
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["node", str(harness_js), str(cfg_path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node UI runner failed (rc={proc.returncode}): stdout={proc.stdout} stderr={proc.stderr}"
            )
        return json.loads(proc.stdout)


class TestOperatorControlUi(unittest.TestCase):
    def _start_server(self, *, planner_mode: str = "priority", state_obj: GameState | None = None):
        self._tmp = tempfile.TemporaryDirectory(prefix="si_ui_srv_")
        cfg_path = Path(self._tmp.name) / "config.yaml"
        cfg_path.write_text(
            f"planner_mode: {planner_mode}\nallow_galaxy_scan: true\nallow_raids: true\nga_seed: 42\nga_generations: 4\nga_population_size: 8\n",
            encoding="utf-8",
        )
        st = state_obj or _sample_state()
        fake_client = MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
        fake_client.read_full_snapshot.return_value = st
        fake_client.navigate_menu.return_value = "<html>ok</html>"

        bridge = GameApiBridge(
            debug_port=9222,
            config_path=cfg_path,
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )
        server = create_api_server(bridge, host="127.0.0.1", port=0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return bridge, server, f"http://127.0.0.1:{port}"

    def test_static_html_css_js_contracts_and_single_svg_invariant(self) -> None:
        bridge, server, base_url = self._start_server()
        try:
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                html_r = requests.get(f"{base_url}/", timeout=5)
                css_r = requests.get(f"{base_url}/styles.css", timeout=5)
                js_r = requests.get(f"{base_url}/app.js", timeout=5)
                ver_r = requests.get(f"{base_url}/ui-version", timeout=5)
            self.assertEqual(html_r.status_code, 200)
            self.assertEqual(css_r.status_code, 200)
            self.assertEqual(js_r.status_code, 200)
            self.assertEqual(ver_r.status_code, 200)
            self.assertTrue(ver_r.json()["ok"])

            html_txt = html_r.text
            css_txt = re.sub(r"/\*.*?\*/", "", css_r.text, flags=re.DOTALL)
            js_txt = js_r.text

            parser = _TableInspector()
            parser.feed(html_txt)
            by_tbody = {t["tbody_id"]: t for t in parser.tables if t["tbody_id"]}

            dash_tbl = by_tbody["dash-plan-tbody"]
            self.assertEqual(len(dash_tbl["headers"]), 6)
            self.assertEqual(dash_tbl["placeholder_colspans"], [6])
            self.assertIn("wait", " ".join(dash_tbl["headers"]).lower())
            self.assertIn("total", " ".join(dash_tbl["headers"]).lower())

            ops_tbl = by_tbody["plan-tbody"]
            self.assertEqual(len(ops_tbl["headers"]), 7)
            self.assertEqual(ops_tbl["placeholder_colspans"], [7])
            self.assertIn("wait", " ".join(ops_tbl["headers"]).lower())
            self.assertIn("total", " ".join(ops_tbl["headers"]).lower())

            self.assertTrue(parser.has_planner_select)
            self.assertEqual(parser.planner_options, ["priority", "ga_nn"])
            for req_id in (
                "gann-calc-hud",
                "gann-radial-svg",
                "dash-planner-mode-badge",
                "ops-planner-mode-badge",
                "btn-scan-galaxy",
                "cmb-targets-tab",
                "cmb-pane-targets",
                "cmb-targets-tbody",
            ):
                self.assertIn(req_id, parser.ids)

            # Single-SVG invariant: only rnk-chart + gann-radial-svg across the entire HTML
            svgs = re.findall(r"<svg\b[^>]*>.*?</svg>", html_txt, flags=re.IGNORECASE | re.DOTALL)
            non_chart_svgs = [s for s in svgs if "rnk-chart" not in s]
            self.assertEqual(len(non_chart_svgs), 1)
            self.assertIn("gann-radial-svg", non_chart_svgs[0])
            self.assertGreaterEqual(len(re.findall(r"<(?:circle|path)\b", non_chart_svgs[0])), 2)

            # F21 CSS ground, keyframes, and reduced-motion suppression
            self.assertIn("#06090b", css_txt.lower())
            self.assertRegex(css_txt, r"@keyframes\s+siRingSpinCw")
            rm_blocks = re.findall(
                r"@media\s*\([^)]*prefers-reduced-motion\s*:\s*reduce[^)]*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                css_txt,
                flags=re.IGNORECASE | re.DOTALL,
            )
            self.assertTrue(any("animation: none" in blk for blk in rm_blocks))

            # apiCommand timeout >= 15000ms
            m_to = re.search(r"async function apiCommand\b[\s\S]{0,400}?(\d{4,6})\s*\)", js_txt)
            self.assertIsNotNone(m_to)
            self.assertGreaterEqual(int(m_to.group(1)), 15000)
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_node_vm_eta_columns_bottleneck_pills_cap_and_legacy_null_fallback(self) -> None:
        bridge, server, base_url = self._start_server()
        try:
            synth_plan = [
                {
                    "priority": "P1",
                    "kind": "BUILD",
                    "planet_id": 12476,
                    "planet_coords": "02:380:12",
                    "target_id": 1,
                    "target_name": "Pig iron mine",
                    "target_level": 4,
                    "cost": {"pig_iron": 500, "metal": 0, "kryptonite": 200, "spice": 0},
                    "missing": {"pig_iron": 500, "metal": 0, "kryptonite": 0, "spice": 0},
                    "reason": "Blocked by Blast furnace drain",
                    "executable_now": False,
                    "wait_hours": None,
                    "wait_seconds": None,
                    "wait_eta": "—",
                    "duration_seconds": 120.0,
                    "duration_text": "2m 00s",
                    "total_hours": None,
                    "total_seconds": None,
                    "total_eta": "—",
                    "eta_bottleneck": "pig_iron",
                },
                {
                    "priority": "P2",
                    "kind": "BUILD",
                    "planet_id": 12476,
                    "planet_coords": "02:380:12",
                    "target_id": 3,
                    "target_name": "Kryptonite mine",
                    "target_level": 4,
                    "cost": {"pig_iron": 300, "metal": 100, "kryptonite": 0, "spice": 0},
                    "missing": {"pig_iron": 0, "metal": 100, "kryptonite": 0, "spice": 0},
                    "reason": "Waiting for Metal",
                    "executable_now": False,
                    "wait_hours": 1.5,
                    "wait_seconds": 5400.0,
                    "wait_eta": "1.5h",
                    "duration_seconds": 1800.0,
                    "duration_text": "30m 00s",
                    "total_hours": 2.0,
                    "total_seconds": 7200.0,
                    "total_eta": "2.0h",
                    "eta_bottleneck": "metal",
                },
                {
                    "priority": "P2",
                    "kind": "BUILD",
                    "planet_id": 12476,
                    "planet_coords": "02:380:12",
                    "target_id": 5,
                    "target_name": "Fusion plant",
                    "target_level": 4,
                    "cost": {"pig_iron": 276, "metal": 0, "kryptonite": 121, "spice": 0},
                    "missing": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                    "reason": "Ready timed build",
                    "executable_now": True,
                    "wait_hours": 0.0,
                    "wait_seconds": 0.0,
                    "wait_eta": "Ready",
                    "duration_seconds": 252.0,
                    "duration_text": "4m 12s",
                    "total_hours": 0.07,
                    "total_seconds": 252.0,
                    "total_eta": "4m",
                    "eta_bottleneck": None,
                },
                {
                    "priority": "P2",
                    "kind": "ATTACK",
                    "planet_id": 12476,
                    "planet_coords": "02:380:12",
                    "target_id": None,
                    "target_name": "02:380:04",
                    "target_level": None,
                    "cost": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                    "missing": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                    "reason": "Instant raid",
                    "executable_now": True,
                    "wait_hours": 0.0,
                    "wait_seconds": 0.0,
                    "wait_eta": "Ready",
                    "duration_seconds": 0.0,
                    "duration_text": "Instant",
                    "total_hours": 0.0,
                    "total_seconds": 0.0,
                    "total_eta": "Ready",
                    "eta_bottleneck": None,
                },
                {
                    "priority": "P2",
                    "kind": "BUILD",
                    "planet_id": 12476,
                    "planet_coords": "02:380:12",
                    "target_id": 7,
                    "target_name": "Development center",
                    "target_level": 2,
                    "cost": {"pig_iron": 100, "metal": 0, "kryptonite": 0, "spice": 0},
                    "missing": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                    "reason": "Legacy payload omitting ETA fields",
                    "executable_now": True,
                    "total_eta": None,
                },
            ] + [
                {
                    "priority": "P3",
                    "kind": "BUILD",
                    "planet_id": 12476,
                    "planet_coords": "02:380:12",
                    "target_id": 10 + i,
                    "target_name": f"Extra {i}",
                    "target_level": 1,
                    "cost": {"pig_iron": 50, "metal": 0, "kryptonite": 0, "spice": 0},
                    "missing": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
                    "reason": "Extra",
                    "executable_now": True,
                    "wait_hours": 0.0,
                    "wait_seconds": 0.0,
                    "wait_eta": "Ready",
                    "duration_seconds": 60.0,
                    "duration_text": "1m 00s",
                    "total_hours": 0.017,
                    "total_seconds": 60.0,
                    "total_eta": "1m",
                    "eta_bottleneck": None,
                }
                for i in range(10)
            ]

            patch_js = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                f"  d.plan = {json.dumps(synth_plan)}; "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(
                    base_url,
                    """
                    const ok = await __waitUntil(() => document.querySelectorAll('#plan-tbody tr').length === 15);
                    if (!ok) throw new Error('plan-tbody did not reach 15 rows');
                    const dashRows = Array.from(document.querySelectorAll('#dash-plan-tbody tr')).map((tr) =>
                      Array.from(tr.children).map((td) => ({
                        text: td.textContent.trim(),
                        pills: td.querySelectorAll('.eta-pill').map((p) => ({ text: p.textContent.trim(), cls: p.className })),
                      }))
                    );
                    const opsRows = Array.from(document.querySelectorAll('#plan-tbody tr')).map((tr) =>
                      Array.from(tr.children).map((td) => ({
                        text: td.textContent.trim(),
                        pills: td.querySelectorAll('.eta-pill').map((p) => ({ text: p.textContent.trim(), cls: p.className })),
                      }))
                    );
                    document.querySelector('[data-filter="ready"]').click();
                    await __sleep(20);
                    const readyCount = document.querySelectorAll('#plan-tbody tr').length;
                    const docTxt = document.getElementById('view-stage').textContent;
                    return {
                      dashRows,
                      opsRows,
                      readyCount,
                      navCount: document.getElementById('nav-queue-count').textContent,
                      planBadge: document.getElementById('plan-count-badge').textContent,
                      hasUndef: docTxt.includes('undefined'),
                      hasNull: docTxt.includes('null'),
                    };
                    """,
                    fetch_patch_js=patch_js,
                )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertEqual(len(sc["dashRows"]), 10)
            self.assertEqual(len(sc["opsRows"]), 15)
            self.assertEqual(sc["navCount"], "15")
            self.assertEqual(sc["readyCount"], 13)
            self.assertEqual(sc["planBadge"], "13 TASKS")
            self.assertFalse(sc["hasUndef"])
            self.assertFalse(sc["hasNull"])

            # Row 0: blocked PIG
            self.assertEqual(len(sc["dashRows"][0]), 6)
            self.assertEqual(len(sc["opsRows"][0]), 7)
            r0_wait_pill = sc["dashRows"][0][3]["pills"][0]
            self.assertIn("is-blocked", r0_wait_pill["cls"])
            self.assertIn("— (PIG)", r0_wait_pill["text"])

            # Row 1: waiting MET
            r1_wait_pill = sc["dashRows"][1][3]["pills"][0]
            self.assertIn("is-wait", r1_wait_pill["cls"])
            self.assertIn("1.5h (MET)", r1_wait_pill["text"])

            # Row 2: timed build in operations shows Dur: 4m 12s
            self.assertIn("4m", sc["opsRows"][2][4]["text"])
            self.assertIn("Dur: 4m 12s", sc["opsRows"][2][4]["text"])

            # Row 3: instant raid
            self.assertIn("Instant", sc["opsRows"][3][4]["text"])
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_node_vm_config_dirty_guard_blank_seed_and_rate_limit_warn_banner(self) -> None:
        bridge, server, base_url = self._start_server(planner_mode="priority")
        try:
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(
                    base_url,
                    """
                    const ready = await __waitUntil(() => state.dashboard !== null);
                    if (!ready) throw new Error('Dashboard failed to load');
                    document.querySelector('[data-page="config"]').click();
                    const sel = document.getElementById('cfg-planner_mode');
                    const seedInp = document.getElementById('cfg-ga_seed');
                    sel.value = 'ga_nn';
                    sel.dispatchEvent('change');
                    seedInp.value = '';
                    seedInp.dispatchEvent('change');

                    const dashBefore = fetchLog.filter((f) => f.url.startsWith('/dashboard')).length;
                    document.getElementById('btn-sync').click();
                    await __waitUntil(() => fetchLog.filter((f) => f.url.startsWith('/dashboard')).length > dashBefore);
                    const preserved = sel.value;

                    document.getElementById('btn-save-config').click();
                    await __waitUntil(() => fetchLog.some((f) => f.url === '/command' && f.body && f.body.command === 'update_config'));
                    await __waitUntil(() => state.dashboard && state.dashboard.config && state.dashboard.config.planner_mode === 'ga_nn');
                    const savedReq = fetchLog.find((f) => f.url === '/command' && f.body && f.body.command === 'update_config').body;
                    return { preserved, savedSeed: savedReq.config.ga_seed, savedMode: savedReq.config.planner_mode };
                    """,
                )
            self.assertEqual(res["errors"], [])
            self.assertEqual(res["scenario"]["preserved"], "ga_nn")
            self.assertEqual(res["scenario"]["savedMode"], "ga_nn")
            self.assertIsNone(res["scenario"]["savedSeed"])

            # Rate limit 429 on scan_galaxy produces .is-warn banner and keeps Chrome ONLINE
            bridge.record_rate_limit(42.0)
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                rl_res = _run_node_ui(
                    base_url,
                    """
                    const ready = await __waitUntil(() => state.dashboard !== null);
                    if (!ready) throw new Error('Dashboard failed to load');
                    document.querySelector('[data-page="combat"]').click();
                    document.getElementById('btn-scan-galaxy').click();
                    await __waitUntil(() => {
                      const b = document.getElementById('feedback-banner');
                      return b && !b.hidden && !b.textContent.includes('Scanning home Galaxy');
                    });
                    return {
                      chromeText: document.getElementById('chrome-status-text').textContent,
                      bannerHidden: document.getElementById('feedback-banner').hidden,
                      bannerClass: document.getElementById('feedback-banner').className,
                    };
                    """,
                )
            self.assertEqual(rl_res["errors"], [])
            self.assertTrue(any(d >= 15000 for d in rl_res["recordedTimeoutDelays"]))
            self.assertIn("ONLINE", rl_res["scenario"]["chromeText"])
            self.assertFalse(rl_res["scenario"]["bannerHidden"])
            self.assertIn("is-warn", rl_res["scenario"]["bannerClass"])
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_node_vm_gann_hud_visibility_clamping_and_recalc(self) -> None:
        # 1) Hidden in priority mode
        _, server_pri, url_pri = self._start_server(planner_mode="priority")
        try:
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                r_pri = _run_node_ui(
                    url_pri,
                    """
                    await __waitUntil(() => state.dashboard !== null);
                    const hud = document.getElementById('gann-calc-hud');
                    return {
                      hudHidden: hud.hidden,
                      hudClass: hud.className,
                      dashBadge: document.getElementById('dash-planner-mode-badge').textContent,
                      opsBadge: document.getElementById('ops-planner-mode-badge').textContent,
                    };
                    """,
                )
            self.assertEqual(r_pri["errors"], [])
            self.assertTrue(r_pri["scenario"]["hudHidden"])
            self.assertEqual(r_pri["scenario"]["dashBadge"], "MODE: PRIORITY")
            self.assertEqual(r_pri["scenario"]["opsBadge"], "MODE: PRIORITY")
        finally:
            server_pri.shutdown()
            server_pri.server_close()
            self._tmp.cleanup()

        # 2) Visible in ga_nn mode even with omitted summary, clamps huge improvement_pct, and supports Recalculate
        _, server_ga, url_ga = self._start_server(planner_mode="ga_nn")
        try:
            patch_ga = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                "  if (!globalThis.__dashHits) { globalThis.__dashHits = 1; delete d.optimizer_summary; } "
                "  else { d.optimizer_summary = { generations: 4, population_size: 8, nn_evaluations: 32, baseline_fitness: 0, best_fitness: 500, improvement_pct: 5e9, best_chromosome: [{kind:'BUILD'},{kind:'ATTACK'}] }; } "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                r_ga = _run_node_ui(
                    url_ga,
                    """
                    await __waitUntil(() => state.dashboard !== null);
                    const hud = document.getElementById('gann-calc-hud');
                    const hiddenOmitted = hud.hidden;
                    const classOmitted = hud.className;
                    const textOmitted = hud.textContent;
                    document.getElementById('btn-sync').click();
                    await __waitUntil(() => document.getElementById('gann-fit-val').textContent.includes('999.9%'));
                    const fitClamped = document.getElementById('gann-fit-val').textContent;
                    const genTxt = document.getElementById('gann-gen-val').textContent;
                    const nnTxt = document.getElementById('gann-nn-val').textContent;
                    const genomeTxt = document.getElementById('gann-genome-val').textContent;

                    document.getElementById('btn-gann-recalc').click();
                    await __waitUntil(() => fetchLog.some((f) => f.url === '/command' && f.body && f.body.command === 'plan' && f.body.planner_mode === 'ga_nn'));
                    await __waitUntil(() => !state.gaCalcRunning);
                    return {
                      hiddenOmitted,
                      classOmitted,
                      textOmitted,
                      fitClamped,
                      genTxt,
                      nnTxt,
                      genomeTxt,
                      hudTextAfter: hud.textContent,
                      dashBadge: document.getElementById('dash-planner-mode-badge').textContent,
                    };
                    """,
                    fetch_patch_js=patch_ga,
                )
            self.assertEqual(r_ga["errors"], [])
            sc = r_ga["scenario"]
            self.assertFalse(sc["hiddenOmitted"])
            self.assertIn("is-active", sc["classOmitted"])
            self.assertRegex(sc["textOmitted"], r"[1-9]")
            self.assertNotIn("undefined", sc["hudTextAfter"])
            self.assertNotIn("NaN", sc["hudTextAfter"])
            self.assertEqual(sc["fitClamped"], ">+999.9%")
            self.assertEqual(sc["genTxt"], "4 / 4")
            self.assertEqual(sc["nnTxt"], "32 evals")
            self.assertEqual(sc["genomeTxt"], "2 genes")
            self.assertEqual(sc["dashBadge"], "MODE: GA+NN")
        finally:
            server_ga.shutdown()
            server_ga.server_close()
            self._tmp.cleanup()

    def test_css_bar_specificity_blocked_contrast_and_mobile_window_bar_wrap(self) -> None:
        _, server, base_url = self._start_server()
        try:
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                html_txt = requests.get(f"{base_url}/", timeout=5).text
                css_txt = re.sub(r"/\*.*?\*/", "", requests.get(f"{base_url}/styles.css", timeout=5).text, flags=re.DOTALL)

            # 1) .meter-fill.gann-bar-{cyan,mint,gold} are qualified and not overridden by .meter-fill
            m_meter = re.search(r"\.meter-fill\s*\{[^}]*background\s*:\s*var\(--ivory\)", css_txt)
            self.assertIsNotNone(m_meter)
            for cls in ("gann-bar-cyan", "gann-bar-mint", "gann-bar-gold"):
                m_bar = re.search(rf"\.meter-fill\.{cls}\s*\{{[^}}]*background\s*:", css_txt)
                self.assertIsNotNone(m_bar, f"Missing qualified .meter-fill.{cls} selector")
                self.assertGreater(m_bar.start(), m_meter.end())

            # 2) .eta-pill.is-blocked uses WCAG AA >= 4.5:1 #ff6873 on #0e1416 and #131b1e
            m_blocked = re.search(r"\.eta-pill\.is-blocked\s*\{([^}]+)\}", css_txt)
            self.assertIsNotNone(m_blocked)
            self.assertIn("#ff6873", m_blocked.group(1).lower())

            def _rel_lum(hex_rgb: str) -> float:
                vals = [int(hex_rgb[i : i + 2], 16) / 255.0 for i in (1, 3, 5)]
                lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in vals]
                return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

            for bg in ("#0e1416", "#131b1e"):
                l1, l2 = _rel_lum("#ff6873"), _rel_lum(bg)
                ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
                self.assertGreaterEqual(ratio, 4.5, f"Contrast {ratio:.2f} < 4.5 on {bg}")

            # 3) #gann-meta-line / .gann-hud .module-meta >= 0.86rem (12px floor)
            self.assertRegex(css_txt, r"(?:\.gann-hud\s+\.module-meta|#gann-meta-line)[^}]*font-size\s*:\s*0\.86rem")

            # 4) Both #gann-calc-hud and NXQ Top Planned Actions .window-bar carry window-bar-wrap
            m_gann = re.search(r'id="gann-calc-hud"[\s\S]{0,400}?class="([^"]*window-bar[^"]*)"', html_txt)
            self.assertIsNotNone(m_gann)
            self.assertIn("window-bar-wrap", m_gann.group(1))
            m_nxq = re.search(r'<span class="module-code tabular">NXQ</span>[\s\S]{0,250}', html_txt)
            self.assertIsNotNone(m_nxq)
            nxq_start = html_txt.rfind('<div class="window-bar', 0, html_txt.find('id="dash-plan-tbody"'))
            nxq_bar_tag = html_txt[nxq_start : html_txt.find(">", nxq_start) + 1]
            self.assertIn("window-bar-wrap", nxq_bar_tag)
            self.assertRegex(css_txt, r"@media\s*\(\s*max-width\s*:\s*680px\s*\)[\s\S]*?\.window-bar\s*\{[^}]*flex-wrap\s*:\s*wrap")
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_node_vm_async_race_input_dirty_guard_and_legacy_blocked_fallback(self) -> None:
        _, server, base_url = self._start_server(planner_mode="priority")
        try:
            # A) Async loadDashboard race: slow initial priority /dashboard must NOT drop post-save reload or revert ga_nn
            race_patch = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  globalThis.__dashCall = (globalThis.__dashCall || 0) + 1; "
                "  const callIdx = globalThis.__dashCall; "
                "  if (callIdx === 1) { "
                "    const r = await nativeFetch(fullUrl, opts); "
                "    const d = await r.json(); "
                "    await new Promise((res) => setTimeout(res, 260)); "
                "    return { ok: true, status: 200, json: async () => d }; "
                "  } "
                "} "
                "if (url === '/command' && opts && opts.body) { "
                "  const b = JSON.parse(String(opts.body)); "
                "  if (b.command === 'update_config') { "
                "    return nativeFetch(fullUrl, opts); "
                "  } "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                r_race = _run_node_ui(
                    base_url,
                    """
                    // Wait until initial /dashboard fetch has fired (still in flight for 260ms)
                    await __waitUntil(() => fetchLog.some((f) => String(f.url || '').startsWith('/dashboard')));
                    const sel = document.getElementById('cfg-planner_mode');
                    sel.value = 'ga_nn';
                    sel.dispatchEvent('change');
                    document.getElementById('btn-save-config').click();
                    await __waitUntil(() => state.dashboard && state.dashboard.config && state.dashboard.config.planner_mode === 'ga_nn');
                    await __sleep(360);
                    return {
                      dashFetches: fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length,
                      selVal: sel.value,
                      dashMode: state.dashboard.config.planner_mode,
                      hudHidden: document.getElementById('gann-calc-hud').hidden,
                      dashBadge: document.getElementById('dash-planner-mode-badge').textContent,
                    };
                    """,
                    fetch_patch_js=race_patch,
                )
            self.assertEqual(r_race["errors"], [])
            sc_race = r_race["scenario"]
            self.assertGreaterEqual(sc_race["dashFetches"], 2)
            self.assertEqual(sc_race["selVal"], "ga_nn")
            self.assertEqual(sc_race["dashMode"], "ga_nn")
            self.assertFalse(sc_race["hudHidden"])
            self.assertEqual(sc_race["dashBadge"], "MODE: GA+NN")

            # B) Cached summary pill ("OPTIMIZED"), "input" dirty guard before blur, legacy !executable_now blocked pill, & refresh=1 vs stale refresh=0
            extra_patch = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  globalThis.__exCall = (globalThis.__exCall || 0) + 1; "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                "  if (d.config) d.config.planner_mode = 'ga_nn'; "
                "  if (globalThis.__exCall === 1) { "
                "    d.optimizer_summary = { generations: 4, population_size: 8, nn_evaluations: 32, baseline_fitness: 100, best_fitness: 150, improvement_pct: 50 }; "
                "    d.plan = [{ priority: 'P2', kind: 'BUILD', planet_id: 12476, planet_coords: '02:380:12', target_id: 1, target_name: 'Legacy Blocked', target_level: 2, cost: { pig_iron: 100 }, missing: { pig_iron: 50 }, reason: 'Legacy', executable_now: false, total_eta: null, eta_bottleneck: 'metal' }]; "
                "  } else if (url.includes('refresh=0')) { "
                "    await new Promise((res) => setTimeout(res, 240)); "
                "    d.state.planets[0].resources.pig_iron = 1111; "
                "    delete d.optimizer_summary; "
                "  } else { "
                "    d.state.planets[0].resources.pig_iron = 9999; "
                "    delete d.optimizer_summary; "
                "  } "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                r_extra = _run_node_ui(
                    base_url,
                    """
                    await __waitUntil(() => state.dashboard !== null);
                    const legacyPill = document.querySelector('#dash-plan-tbody .eta-pill');
                    const legacyClass = legacyPill ? legacyPill.className : '';
                    const legacyText = legacyPill ? legacyPill.textContent.trim() : '';

                    // Type into #cfg-ga_seed with 'input' only (no 'change' / blur)
                    const seedInp = document.getElementById('cfg-ga_seed');
                    seedInp.value = '999';
                    seedInp.dispatchEvent('input');
                    const dirtyAfterInput = state.configDirty;

                    // Trigger slow refresh=0 followed immediately by forced refresh=1 (#btn-sync)
                    loadDashboard({ refresh: false, preserveConfig: true });
                    await __sleep(20);
                    document.getElementById('btn-sync').click();
                    await __waitUntil(() => document.getElementById('val-pig').textContent === '9,999');
                    await __sleep(320);

                    return {
                      legacyClass,
                      legacyText,
                      dirtyAfterInput,
                      seedAfterPolls: seedInp.value,
                      pigAfterStaleResolves: document.getElementById('val-pig').textContent,
                      statusPillCached: document.getElementById('gann-status-pill').textContent,
                    };
                    """,
                    fetch_patch_js=extra_patch,
                )
            self.assertEqual(r_extra["errors"], [])
            sc_ex = r_extra["scenario"]
            self.assertIn("is-blocked", sc_ex["legacyClass"])
            self.assertEqual(sc_ex["legacyText"], "— (MET)")
            self.assertTrue(sc_ex["dirtyAfterInput"])
            self.assertEqual(sc_ex["seedAfterPolls"], "999")
            self.assertEqual(sc_ex["pigAfterStaleResolves"], "9,999")
            self.assertEqual(sc_ex["statusPillCached"], "OPTIMIZED")
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_node_vm_gann_recalc_mid_poll_no_deadlock_legacy_zero_wait_and_responsive_css(self) -> None:
        _, server, base_url = self._start_server(planner_mode="ga_nn")
        try:
            # 1) Verify @media (max-width: 1100px) resets flex: 0 0 auto (preventing 0px table collapse)
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                css_txt = re.sub(
                    r"/\*.*?\*/",
                    "",
                    requests.get(f"{base_url}/styles.css", timeout=5).text,
                    flags=re.DOTALL,
                )
            m_1100 = re.search(
                r"@media\s*\(\s*max-width\s*:\s*1100px\s*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                css_txt,
                flags=re.IGNORECASE | re.DOTALL,
            )
            self.assertIsNotNone(m_1100)
            blk_1100 = m_1100.group(1)
            for sel in (".table-scroll", ".page-fill", ".dash-lower-grid", ".split-workspace"):
                self.assertIn(sel, blk_1100)
            self.assertRegex(blk_1100, r"flex\s*:\s*0\s+0\s+auto")
            self.assertRegex(blk_1100, r"\.table-scroll\s*\{[^}]*overflow-x\s*:\s*auto")
            self.assertRegex(blk_1100, r"\.table-scroll\s*\{[^}]*overflow-y\s*:\s*visible")

            # 2) #btn-gann-recalc overlapping slow background /dashboard poll MUST NOT deadlock inFlightDashboard
            #    AND legacy { executable_now: false, wait_hours: 0, eta_bottleneck: "pig_iron" } renders .is-blocked "— (PIG)"
            mid_poll_patch = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  globalThis.__dashHits = (globalThis.__dashHits || 0) + 1; "
                "  const hit = globalThis.__dashHits; "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                "  if (hit === 1) { "
                "    d.plan = [{ priority: 'P1', kind: 'BUILD', planet_id: 12476, planet_coords: '02:380:12', target_id: 3, target_name: 'Legacy Zero Wait', target_level: 4, cost: { pig_iron: 100 }, missing: { pig_iron: 50 }, reason: 'Blocked', executable_now: false, wait_hours: 0, eta_bottleneck: 'pig_iron' }]; "
                "  } else if (hit === 2) { "
                "    await new Promise((res) => setTimeout(res, 240)); "
                "    d.state.planets[0].resources.pig_iron = 1111; "
                "  } else if (hit === 3) { "
                "    d.state.planets[0].resources.pig_iron = 7777; "
                "  } "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} "
                "if (url === '/command' && opts && opts.body) { "
                "  const b = JSON.parse(String(opts.body)); "
                "  if (b.command === 'plan') { "
                "    await new Promise((res) => setTimeout(res, 25)); "
                "    return nativeFetch(fullUrl, opts); "
                "  } "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                r_mid = _run_node_ui(
                    base_url,
                    """
                    await __waitUntil(() => state.dashboard !== null);
                    const legZeroPill = document.querySelector('#dash-plan-tbody .eta-pill');
                    const legZeroClass = legZeroPill ? legZeroPill.className : '';
                    const legZeroText = legZeroPill ? legZeroPill.textContent.trim() : '';

                    const slowPoll = loadDashboard({ refresh: false, preserveConfig: true });
                    await __sleep(20);
                    const inFlightDuringPoll = state.inFlightDashboard;
                    document.getElementById('btn-gann-recalc').click();
                    await __waitUntil(() => !state.gaCalcRunning);
                    await slowPoll;
                    const inFlightAfterStaleSettled = state.inFlightDashboard;
                    const countAfterStaleSettled = state.inFlightDashboardCount;

                    const dashBefore = fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length;
                    const nextPollOk = await loadDashboard({ refresh: false, preserveConfig: true });
                    const dashAfter = fetchLog.filter((f) => String(f.url || '').startsWith('/dashboard')).length;

                    return {
                      legZeroClass,
                      legZeroText,
                      inFlightDuringPoll,
                      inFlightAfterStaleSettled,
                      countAfterStaleSettled,
                      nextPollOk,
                      dashDelta: dashAfter - dashBefore,
                      pigAfterNextPoll: document.getElementById('val-pig').textContent,
                    };
                    """,
                    fetch_patch_js=mid_poll_patch,
                )
            self.assertEqual(r_mid["errors"], [])
            sc_mid = r_mid["scenario"]
            self.assertIn("is-blocked", sc_mid["legZeroClass"])
            self.assertEqual(sc_mid["legZeroText"], "— (PIG)")
            self.assertTrue(sc_mid["inFlightDuringPoll"])
            self.assertFalse(sc_mid["inFlightAfterStaleSettled"])
            self.assertEqual(sc_mid["countAfterStaleSettled"], 0)
            self.assertTrue(sc_mid["nextPollOk"])
            self.assertEqual(sc_mid["dashDelta"], 1)
            self.assertEqual(sc_mid["pigAfterNextPoll"], "7,777")

            # 3) #btn-gann-recalc completing during slow initial boot (/dashboard still null) still hydrates dashboard
            boot_patch = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  globalThis.__bootHits = (globalThis.__bootHits || 0) + 1; "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                "  if (globalThis.__bootHits === 1) { "
                "    await new Promise((res) => setTimeout(res, 260)); "
                "    d.state.planets[0].resources.pig_iron = 1111; "
                "  } else { "
                "    d.state.planets[0].resources.pig_iron = 6666; "
                "  } "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                r_boot = _run_node_ui(
                    base_url,
                    """
                    await __waitUntil(() => fetchLog.some((f) => String(f.url || '').startsWith('/dashboard')));
                    const wasNullBeforeClick = state.dashboard === null;
                    document.getElementById('btn-gann-recalc').click();
                    await __waitUntil(() => !state.gaCalcRunning && state.dashboard !== null);
                    await __sleep(300);
                    const followUpOk = await loadDashboard({ refresh: false, preserveConfig: true });
                    return {
                      wasNullBeforeClick,
                      hydratedAfterRecalc: state.dashboard !== null,
                      inFlightAfter: state.inFlightDashboard,
                      followUpOk,
                      pigVal: document.getElementById('val-pig').textContent,
                    };
                    """,
                    fetch_patch_js=boot_patch,
                )
            self.assertEqual(r_boot["errors"], [])
            sc_boot = r_boot["scenario"]
            self.assertTrue(sc_boot["wasNullBeforeClick"])
            self.assertTrue(sc_boot["hydratedAfterRecalc"])
            self.assertFalse(sc_boot["inFlightAfter"])
            self.assertTrue(sc_boot["followUpOk"])
            self.assertEqual(sc_boot["pigVal"], "6,666")
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_adv_ui_gacalc_refcount_fmt_neg_zero_finite_num_and_responsive_css(self) -> None:
        _, server, base_url = self._start_server(planner_mode="ga_nn")
        try:
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                css_txt = re.sub(
                    r"/\*.*?\*/",
                    "",
                    requests.get(f"{base_url}/styles.css", timeout=5).text,
                    flags=re.DOTALL,
                )
            self.assertRegex(css_txt, r"\.nav-title\s*\{[^}]*font-size\s*:\s*0\.86rem")
            self.assertRegex(css_txt, r"\.eta-pill\s*\{[^}]*font-size\s*:\s*0\.86rem")
            m_1100 = re.search(
                r"@media\s*\(\s*max-width\s*:\s*1100px\s*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                css_txt,
                flags=re.IGNORECASE | re.DOTALL,
            )
            self.assertIsNotNone(m_1100)
            m_logs = re.search(r"#logs-pre\s*\{[^}]*min-height\s*:\s*(\d+)px", m_1100.group(1))
            self.assertIsNotNone(m_logs)
            self.assertGreaterEqual(int(m_logs.group(1)), 200)

            patch_concurrent = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url === '/command' && opts && opts.body) { "
                "  const b = JSON.parse(String(opts.body)); "
                "  if (b.command === 'plan') { "
                "    return new Promise((resolve) => { "
                "      globalThis.__resolveSlowPlan = async () => resolve(await nativeFetch(fullUrl, opts)); "
                "    }); "
                "  } "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(
                    base_url,
                    """
                    await __waitUntil(() => state.dashboard !== null);
                    const negZero = fmtImprovementPct(-0.01, 100, 99.99);
                    const fnArr = finiteNum([], 'FB');
                    const fnObj = finiteNum({}, 'FB');
                    const fnBlank = finiteNum('   ', 'FB');

                    document.getElementById('btn-gann-recalc').click();
                    await __sleep(10);
                    document.getElementById('btn-sync').click();
                    await __waitUntil(() => document.getElementById('feedback-banner').textContent.includes('Synced') && !state.inFlightDashboard);
                    await __sleep(10);
                    const runningAfterSyncSettled = state.gaCalcRunning;
                    const countAfterSyncSettled = state.gaCalcCount;
                    const hudHasCalculating = document.getElementById('gann-calc-hud').classList.contains('is-calculating');

                    globalThis.__resolveSlowPlan();
                    await __waitUntil(() => !state.gaCalcRunning);
                    const runningAfterRecalcSettled = state.gaCalcRunning;
                    const countAfterRecalcSettled = state.gaCalcCount;
                    return {
                      negZero,
                      fnArr,
                      fnObj,
                      fnBlank,
                      runningAfterSyncSettled,
                      countAfterSyncSettled,
                      hudHasCalculating,
                      runningAfterRecalcSettled,
                      countAfterRecalcSettled,
                    };
                    """,
                    fetch_patch_js=patch_concurrent,
                )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertEqual(sc["negZero"], "+0.0%")
            self.assertEqual(sc["fnArr"], "FB")
            self.assertEqual(sc["fnObj"], "FB")
            self.assertEqual(sc["fnBlank"], "FB")
            self.assertTrue(sc["runningAfterSyncSettled"])
            self.assertEqual(sc["countAfterSyncSettled"], 1)
            self.assertTrue(sc["hudHasCalculating"])
            self.assertFalse(sc["runningAfterRecalcSettled"])
            self.assertEqual(sc["countAfterRecalcSettled"], 0)
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    def test_ui_1e400_nan_payload_and_non_json_error_no_nan_or_infinity(self) -> None:
        bridge, server, base_url = self._start_server()
        try:
            chaos_patch = (
                "async (url, opts, fullUrl, nativeFetch) => { "
                "if (url.startsWith('/dashboard')) { "
                "  const r = await nativeFetch(fullUrl, opts); "
                "  const d = await r.json(); "
                "  d.state.planets[0].energy = { produced: 1e400, used: 1e400 }; "
                "  d.state.planets[0].fields_used = 1e400; "
                "  d.state.planets[0].fields_max = 1e400; "
                "  d.state.planets[0].resources.pig_iron = 1e400; "
                "  d.state.planets[0].storage.pig_iron = 1e400; "
                "  d.ranking = { "
                "    current: { estimated_points: 1e400, buildings_points: 1e400, research_points: 0, baseline_points: 1e400, server_rank: 1e400, server_points: 1e400, spice_value: 1e400, attack_zone: { label: 'AZ 1', range_text: '0-500' } }, "
                "    rates: { pts_per_hr_steady: 1e400, net_resources_per_hr: 1e400, spice_eq_per_hr: 1e400, speedup_factor: 1 }, "
                "    horizons: [{ label: '24h', steady_points: NaN, steady_delta: Infinity, steady_az: 'AZ 1', compound_points: 100, compound_delta: 10, compound_az: 'AZ 1' }], "
                "    milestones: [], "
                "    observed_trend: { sample_count: 2, delta_points: NaN, observed_pts_per_hr: Infinity, elapsed_hours: NaN, samples: [{ estimated_points: NaN }, { estimated_points: Infinity }] } "
                "  }; "
                "  return { ok: true, status: 200, json: async () => d }; "
                "} "
                "if (url === '/command') { "
                "  return { ok: false, status: 502, text: async () => '<html><body>502 Bad Gateway</body></html>' }; "
                "} }"
            )
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(
                    base_url,
                    """
                    await __waitUntil(() => state.dashboard !== null);
                    setActivePage('ranking');
                    await __sleep(20);
                    const valEnergy = document.getElementById('val-energy').textContent;
                    const energyRatio = document.getElementById('energy-ratio').textContent;
                    const planetSpecs = document.getElementById('planet-specs').textContent;
                    const valPig = document.getElementById('val-pig').textContent;
                    const capPig = document.getElementById('cap-pig').textContent;
                    const rnkBreakdown = document.getElementById('rnk-breakdown').textContent;
                    const rnkServerPos = document.getElementById('rnk-server-pos').textContent;
                    const rnkChartHtml = document.getElementById('rnk-chart').textContent;
                    const horizonsText = document.getElementById('rnk-horizons-tbody').textContent;
                    const observedRate = document.getElementById('rnk-observed-rate').textContent;

                    document.getElementById('btn-tick-now').click();
                    await __waitUntil(() => document.getElementById('feedback-banner').textContent.includes('Tick failed'));
                    const bannerText = document.getElementById('feedback-banner').textContent;
                    return {
                      valEnergy,
                      energyRatio,
                      planetSpecs,
                      valPig,
                      capPig,
                      rnkBreakdown,
                      rnkServerPos,
                      rnkChartHtml,
                      horizonsText,
                      observedRate,
                      bannerText,
                    };
                    """,
                    fetch_patch_js=chaos_patch,
                )
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            for field_name, txt in sc.items():
                self.assertNotIn("NaN", txt, f"Found NaN in {field_name}: {txt!r}")
                self.assertNotIn("∞", txt, f"Found ∞ in {field_name}: {txt!r}")
                self.assertNotIn("Infinity", txt, f"Found Infinity in {field_name}: {txt!r}")
            self.assertIn("HTTP 502", sc["bannerText"])
            self.assertNotIn("Unexpected token", sc["bannerText"])
        finally:
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    def test_bug03_non_dict_json_responses_and_negative_zero_formatting_in_node(self) -> None:
        bridge, server, base_url = self._start_server()
        try:
            scenario_js = r"""
const cases = [
  { status: 200, ok: true, body: "null" },
  { status: 500, ok: false, body: "null" },
  { status: 200, ok: true, body: "[]" },
  { status: 502, ok: false, body: '["bad"]' },
  { status: 429, ok: false, body: '["rate"]' },
  { status: 200, ok: true, body: '"bad"' },
  { status: 200, ok: true, body: "123" },
];
const parseResults = [];
for (const c of cases) {
  const mockRes = {
    ok: c.ok,
    status: c.status,
    async text() { return c.body; },
  };
  try {
    await parseResponseJson(mockRes);
    parseResults.push({ status: c.status, threw: false });
  } catch (e) {
    parseResults.push({ status: c.status, threw: true, isTypeErr: e instanceof TypeError, msg: String(e.message || e) });
  }
}

// .json()-only mock responses without .text()
const jsonOnlyResults = [];
for (const payload of [null, [], "bad", 42]) {
  const mockRes = { ok: true, status: 200, async json() { return payload; } };
  try {
    await parseResponseJson(mockRes);
    jsonOnlyResults.push({ threw: false });
  } catch (e) {
    jsonOnlyResults.push({ threw: true, isTypeErr: e instanceof TypeError, msg: String(e.message || e) });
  }
}

// apiGet & apiCommand on 502 ["bad"], 500 null, 200 null, 429 ["rate"], plus .json()-only mocks
const apiResults = [];
const mockPayloads = [
  { ok: false, status: 502, body: '["bad"]' },
  { ok: false, status: 500, body: "null" },
  { ok: true, status: 200, body: "null" },
  { ok: false, status: 429, body: '["rate"]' },
  { ok: true, status: 200, jsonOnly: true, jsonVal: null },
  { ok: false, status: 500, jsonOnly: true, jsonVal: [] },
];
for (const mp of mockPayloads) {
  globalThis.__bug03Mock = mp;
  let getErr = null;
  try {
    await apiGet("/state");
  } catch (e) {
    getErr = { status: e.status, rateLimited: Boolean(e.rateLimited), msg: String(e.message || e), isTypeErr: e instanceof TypeError };
  }
  let cmdErr = null;
  try {
    await apiCommand({ command: "plan" });
  } catch (e) {
    cmdErr = { status: e.status, rateLimited: Boolean(e.rateLimited), msg: String(e.message || e), isTypeErr: e instanceof TypeError };
  }
  apiResults.push({ expectedStatus: mp.status, getErr, cmdErr });
}
globalThis.__bug03Mock = null;

// Exercise 429 warn banner vs error banner
const err429 = new Error("HTTP 429");
err429.status = 429;
err429.rateLimited = true;
showFeedback(err429.message, feedbackKindForError(err429));
const warnClass = el("feedback-banner").className;

// Numeric -0 / -0.1 / negative steady rate formatting checks
renderResourceBar("pig", 0, 1000, -0.1);
const tinyNegRateText = el("rate-pig").textContent;
const tinyNegRateClass = el("rate-pig").className;

renderResourceBar("metal", 0, 1000, -1.0);
const negRateText = el("rate-metal").textContent;
const negRateClass = el("rate-metal").className;

renderRanking({ ranking: { current: { estimated_points: 10 }, rates: { pts_per_hr_steady: -1.5, net_resources_per_hr: -0.1, speedup_factor: 1 }, observed_trend: {} } });
const steadyText = el("rnk-rate-hr").textContent;

renderRanking({ ranking: { current: { estimated_points: 10 }, rates: { pts_per_hr_steady: -0.001, net_resources_per_hr: 0, speedup_factor: 1 }, observed_trend: {} } });
const zeroSteadyText = el("rnk-rate-hr").textContent;

return {
  parseResults,
  jsonOnlyResults,
  apiResults,
  warnClass,
  fmtIntNegZero: fmtInt(-0),
  fmtRateNegZero: fmtRate(-0),
  fmtRateTinyNeg: fmtRate(-0.1),
  tinyNegRateText,
  tinyNegRateClass,
  negRateText,
  negRateClass,
  steadyText,
  zeroSteadyText,
};
"""
            fetch_patch_js = """
async (url) => {
  const mp = globalThis.__bug03Mock;
  if (!mp || (url !== "/state" && url !== "/command")) return undefined;
  if (mp.jsonOnly) {
    return { ok: mp.ok, status: mp.status, async json() { return mp.jsonVal; } };
  }
  return { ok: mp.ok, status: mp.status, async text() { return mp.body; } };
}
"""
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(base_url, scenario_js, fetch_patch_js=fetch_patch_js)
            sc = res["scenario"]
            for item in sc["parseResults"]:
                self.assertTrue(item["threw"])
                self.assertFalse(item["isTypeErr"])
                self.assertNotIn("Cannot read properties of null", item["msg"])
            for item in sc["jsonOnlyResults"]:
                self.assertTrue(item["threw"])
                self.assertFalse(item["isTypeErr"])
            for item in sc["apiResults"]:
                for key in ("getErr", "cmdErr"):
                    err = item[key]
                    self.assertIsNotNone(err)
                    self.assertFalse(err["isTypeErr"])
                    self.assertNotIn("Cannot read properties of null", err["msg"])
                    if item["expectedStatus"] == 429:
                        self.assertTrue(err["rateLimited"])
            self.assertIn("is-warn", sc["warnClass"])
            self.assertEqual(sc["fmtIntNegZero"], "0")
            self.assertEqual(sc["fmtRateNegZero"], "0/h")
            self.assertEqual(sc["fmtRateTinyNeg"], "0/h")
            self.assertEqual(sc["tinyNegRateText"], "0/h")
            self.assertNotIn("is-neg", sc["tinyNegRateClass"])
            self.assertEqual(sc["negRateText"], "-1/h")
            self.assertIn("is-neg", sc["negRateClass"])
            self.assertEqual(sc["steadyText"], "-1.50 pts/hr")
            self.assertEqual(sc["zeroSteadyText"], "+0.00 pts/hr")
        finally:
            bridge.close()
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    def test_r7_ui_corrupt_bf_pct_subthreshold_neg_zero_ranking_signs_and_impr_pct_undefined(self) -> None:
        self.assertIsNotNone(shutil.which("node"), "Node.js is required")
        bridge, server, base_url = self._start_server(planner_mode="ga_nn")
        try:
            scenario_js = r"""
await __waitUntil(() => state.dashboard !== null);

const fmtSmallNeg = fmtInt(-0.0001);
const fmtPointOne = fmtInt(-0.1);
const fmtRatePointOne = fmtRate(-0.1);
const imprUndef = fmtImprovementPct(null, undefined, undefined);
const imprTinyNeg = fmtImprovementPct(-0.01, 100, 99.99);

const bfFallback = getSelectedBlastFurnacePct({
  state: { planets: [{ planet_id: 1, economy_rates: { resource2: "bad" } }] },
  buildings_eta: { planet: { planet_id: 1 }, planet_economy: { blast_furnace_pct: 0 } },
});

renderTelemetry({
  state: state.dashboard.state,
  buildings_eta: state.dashboard.buildings_eta,
  ranking: { current: { estimated_points: 10 }, rates: { pts_per_hr_steady: -0.0049, spice_eq_per_hr: 10 } },
  health: state.dashboard.health,
});
const dashSteadyText = el("dash-pts-hr").textContent;

renderBuildingsView({
  state: state.dashboard.state,
  buildings_eta: {
    planet: { coords: "01:001:01", building_time_divisor: "bad" },
    planet_economy: { blast_furnace_pct: "bad" },
    buildings: [
      {
        key: undefined,
        name: "Blast furnace",
        economy_rate_pct: "bad",
        current_level: 4,
        next_level: 5,
        missing_total: 0,
        build_time_text: "1m",
        wait_eta: "Ready",
        reach_eta: "1m",
        wait_hours: 0,
      },
    ],
    researches: [],
  },
});
const bldMeta = el("bld-planet-meta").textContent;
const bldRowText = el("eta-table-tbody").textContent;
const pauseDisBad = el("btn-b2-pause").disabled;
const resumeDisBad = el("btn-b2-resume").disabled;

renderBuildingsView({
  state: state.dashboard.state,
  buildings_eta: {
    planet: { coords: "01:001:01", building_time_divisor: 2 },
    planet_economy: { blast_furnace_pct: 0 },
    buildings: [],
    researches: [],
  },
});
const pauseDisZero = el("btn-b2-pause").disabled;
const resumeDisZero = el("btn-b2-resume").disabled;

renderGaNnHud({
  config: { planner_mode: "ga_nn" },
  optimizer_summary: { elapsed_ms: -5, nn_final_loss: -1e-6, population_size: 8, nn_backend: "python" },
});
const hudMeta = el("gann-meta-line").textContent;
const hudNnSub = el("gann-nn-sub").textContent;

renderRanking({
  ranking: {
    current: { estimated_points: -0.01, attack_zone: { label: "AZ 1" } },
    rates: { pts_per_hr_steady: -0.001, net_resources_per_hr: 0, speedup_factor: 1 },
    horizons: [
      {
        label: "+24 Hours",
        steady_points: 90,
        steady_delta: -10,
        steady_az: "AZ 1",
        compound_points: 85,
        compound_delta: -15,
        compound_az: "AZ 1",
      },
    ],
    milestones: [],
    observed_trend: {
      sample_count: 2,
      delta_points: 0,
      observed_pts_per_hr: -0.5,
      elapsed_hours: 1,
      rank_climbed: -0,
      samples: [{ estimated_points: 10 }, { estimated_points: 10 }],
    },
  },
});
const rnkEstPts = el("rnk-est-pts").textContent;
const rnkObsRate = el("rnk-observed-rate").textContent;
const rnkObsSummary = el("rnk-observed-summary").textContent;
const rnkHorizons = el("rnk-horizons-tbody").textContent;

return {
  fmtSmallNeg,
  fmtPointOne,
  fmtRatePointOne,
  imprUndef,
  imprTinyNeg,
  bfFallback,
  dashSteadyText,
  bldMeta,
  bldRowText,
  pauseDisBad,
  resumeDisBad,
  pauseDisZero,
  resumeDisZero,
  hudMeta,
  hudNnSub,
  rnkEstPts,
  rnkObsRate,
  rnkObsSummary,
  rnkHorizons,
};
"""
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(base_url, scenario_js)
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertEqual(sc["fmtSmallNeg"], "0")
            self.assertEqual(sc["fmtPointOne"], "-0.1")
            self.assertEqual(sc["fmtRatePointOne"], "0/h")
            self.assertEqual(sc["imprUndef"], "—")
            self.assertEqual(sc["imprTinyNeg"], "+0.0%")
            self.assertEqual(sc["bfFallback"], 0)
            self.assertEqual(sc["dashSteadyText"], "+0.00/h")
            self.assertNotIn("Div xbad", sc["bldMeta"])
            self.assertIn("Div x1", sc["bldMeta"])
            self.assertNotIn("(undefined)", sc["bldRowText"])
            self.assertNotIn("bad%", sc["bldRowText"])
            self.assertFalse(sc["pauseDisBad"])
            self.assertTrue(sc["resumeDisBad"])
            self.assertTrue(sc["pauseDisZero"])
            self.assertFalse(sc["resumeDisZero"])
            self.assertIn("0ms", sc["hudMeta"])
            self.assertNotIn("-5ms", sc["hudMeta"])
            self.assertIn("MSE Loss 0.0000", sc["hudNnSub"])
            self.assertNotIn("-0.0000", sc["hudNnSub"])
            self.assertTrue(sc["rnkEstPts"].startswith("~0.0"))
            self.assertEqual(sc["rnkObsRate"], "-0.5 pts/hr observed")
            self.assertNotIn("+-", sc["rnkObsSummary"])
            self.assertNotIn("climbed +0", sc["rnkObsSummary"])
            self.assertIn("(-10)", sc["rnkHorizons"])
            self.assertIn("(-15)", sc["rnkHorizons"])
            self.assertNotIn("(+-", sc["rnkHorizons"])
        finally:
            bridge.close()
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    def test_r7_ui_save_config_race_seed_validation_and_immediate_config_sync(self) -> None:
        self.assertIsNotNone(shutil.which("node"), "Node.js is required")
        bridge, server, base_url = self._start_server(planner_mode="priority")
        try:
            race_patch = """
async (url, opts, fullUrl, nativeFetch) => {
  if (globalThis.__dashMode === 'hold_priority' && url.startsWith('/dashboard')) {
    globalThis.__dashMode = 'normal';
    const r = await nativeFetch(fullUrl, opts);
    const d = await r.json();
    d.config = { ...(d.config || {}), planner_mode: 'priority', ga_seed: 42 };
    d.planner_mode = 'priority';
    return new Promise((resolve) => {
      globalThis.__resolveHeldDash = () => resolve({ ok: true, status: 200, json: async () => d });
    });
  }
  if (globalThis.__dashMode === 'fail_503' && url.startsWith('/dashboard')) {
    return { ok: false, status: 503, json: async () => ({ ok: false, error: 'transient 503' }) };
  }
}
"""
            scenario_js = r"""
await __waitUntil(() => state.dashboard !== null);

// 1. Invalid ga_seed="abc" must NOT coerce to null; server must reject with 400
el("cfg-ga_seed").value = "abc";
el("btn-save-config").click();
await __waitUntil(() => fetchLog.some((f) => f.url === "/command" && f.body?.command === "update_config" && f.body?.config?.ga_seed === "abc"));
await __waitUntil(() => el("feedback-banner").textContent.includes("Failed saving config"));
const badBanner = el("feedback-banner").textContent;

// 2. Race: slow pre-click dashboard resolving after save click + post-save refresh 503
el("cfg-planner_mode").value = "ga_nn";
el("cfg-ga_seed").value = "99";
globalThis.__dashMode = "hold_priority";
const heldPromise = loadDashboard({ refresh: false, preserveConfig: true, force: true });
await __waitUntil(() => typeof globalThis.__resolveHeldDash === "function");

globalThis.__dashMode = "fail_503";
el("btn-save-config").click();
await __sleep(20);
globalThis.__resolveHeldDash();
await heldPromise;

await __waitUntil(() => el("dash-planner-mode-badge").textContent === "MODE: GA+NN" && state.dashboard?.config?.ga_seed === 99);
await __sleep(50);

return {
  badBanner,
  dashMode: state.dashboard?.planner_mode,
  cfgMode: state.dashboard?.config?.planner_mode,
  cfgSeed: state.dashboard?.config?.ga_seed,
  domMode: el("cfg-planner_mode").value,
  domSeed: el("cfg-ga_seed").value,
  badgeText: el("dash-planner-mode-badge").textContent,
};
"""
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(base_url, scenario_js, fetch_patch_js=race_patch)
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertIn("Failed saving config", sc["badBanner"])
            self.assertEqual(sc["dashMode"], "ga_nn")
            self.assertEqual(sc["cfgMode"], "ga_nn")
            self.assertEqual(sc["cfgSeed"], 99)
            self.assertEqual(sc["domMode"], "ga_nn")
            self.assertEqual(sc["domSeed"], "99")
            self.assertEqual(sc["badgeText"], "MODE: GA+NN")
        finally:
            bridge.close()
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    def test_r8_ui_whitespace_bottleneck_and_mid_save_dirtyless_poll_guard(self) -> None:
        self.assertIsNotNone(shutil.which("node"), "Node.js is required")
        bridge, server, base_url = self._start_server(planner_mode="priority")
        try:
            poll_patch = """
async (url, opts, fullUrl, nativeFetch) => {
  if (url === '/command' && opts?.body) {
    const parsed = JSON.parse(opts.body);
    if (parsed.command === 'update_config' && globalThis.__r8SaveMode === 'hold_and_fail') {
      return new Promise((resolve) => {
        globalThis.__resolveR8SaveFail = () => resolve({
          ok: false,
          status: 500,
          json: async () => ({ ok: false, error: 'mid-save transient 500' }),
        });
      });
    }
  }
  if (url.startsWith('/dashboard') && globalThis.__r8DashPrio) {
    const r = await nativeFetch(fullUrl, opts);
    const d = await r.json();
    d.config = { ...(d.config || {}), planner_mode: 'priority' };
    d.planner_mode = 'priority';
    return { ok: true, status: 200, json: async () => d };
  }
}
"""
            scenario_js = r"""
await __waitUntil(() => state.dashboard !== null);

// 1. Whitespace bottleneck in buildings_eta must NOT render "Ready ()"
const wsDash = {
  ...state.dashboard,
  buildings_eta: {
    ...(state.dashboard.buildings_eta || {}),
    planet: state.dashboard.buildings_eta?.planet || { planet_id: 12476 },
    planet_economy: state.dashboard.buildings_eta?.planet_economy || {},
    buildings: [
      {
        building_id: 1,
        key: "b1",
        name: "Pig iron mine",
        category: "mines",
        current_level: 1,
        next_level: 2,
        missing_total: 0,
        build_time_text: "10s",
        wait_eta: "Ready",
        reach_eta: "10s",
        wait_hours: 0,
        bottleneck: "   ",
        can_build_live: false,
        offer_visible: true,
        unmet_prerequisites: [],
      },
    ],
    researches: [],
  },
};
renderBuildingsView(wsDash);
const bldWsText = el("eta-table-tbody").textContent;

// 2. Programmatic dirty-less change + mid-save loadDashboard poll must NOT overwrite form before save failure
el("cfg-planner_mode").value = "ga_nn";
state.configDirty = false;
globalThis.__r8SaveMode = "hold_and_fail";
globalThis.__r8DashPrio = true;

el("btn-save-config").click();
await __waitUntil(() => typeof globalThis.__resolveR8SaveFail === "function");

// Trigger concurrent loadDashboard mid-save returning priority
const midOk = await loadDashboard({ refresh: false, preserveConfig: true, force: true });
const domMid = el("cfg-planner_mode").value;

// Reject update_config and confirm form still keeps "ga_nn"
globalThis.__resolveR8SaveFail();
await __waitUntil(() => state.configSaving === 0 && el("feedback-banner").textContent.includes("Failed saving config"));
const domAfterFail = el("cfg-planner_mode").value;

// Now let update_config succeed normally and confirm saved config applies cleanly
globalThis.__r8SaveMode = "normal";
globalThis.__r8DashPrio = false;
el("btn-save-config").click();
await __waitUntil(() => state.configSaving === 0 && state.dashboard?.config?.planner_mode === "ga_nn");

return {
  bldWsText,
  midOk,
  domMid,
  domAfterFail,
  domAfterSave: el("cfg-planner_mode").value,
  finalMode: state.dashboard?.config?.planner_mode,
};
"""
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(base_url, scenario_js, fetch_patch_js=poll_patch)
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertNotIn("()", sc["bldWsText"])
            self.assertIn("Ready", sc["bldWsText"])
            self.assertTrue(sc["midOk"])
            self.assertEqual(sc["domMid"], "ga_nn")
            self.assertEqual(sc["domAfterFail"], "ga_nn")
            self.assertEqual(sc["domAfterSave"], "ga_nn")
            self.assertEqual(sc["finalMode"], "ga_nn")
        finally:
            bridge.close()
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()


    def test_action_graph_static_html_css_js_and_english_only_contracts(self) -> None:
        from spaceinvasion.api_server import UI_DIR

        html_txt = (UI_DIR / "index.html").read_text(encoding="utf-8")
        css_txt = (UI_DIR / "styles.css").read_text(encoding="utf-8")
        js_txt = (UI_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-page="action-graph"', html_txt)
        self.assertIn('id="nav-graph-badge"', html_txt)
        self.assertIn('data-goto-page="action-graph"', html_txt)
        self.assertIn('id="view-action-graph"', html_txt)
        self.assertIn('id="ag-main-window"', html_txt)
        self.assertIn('id="ag-control-panel"', html_txt)

        svgs = re.findall(r"<svg\b[^>]*>.*?</svg>", html_txt, flags=re.IGNORECASE | re.DOTALL)
        non_chart_svgs = [s for s in svgs if "rnk-chart" not in s]
        self.assertEqual(len(non_chart_svgs), 1)
        main_win_match = re.search(
            r'<article[^>]*id="ag-main-window"[^>]*>(.*?)</article>',
            html_txt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        self.assertIsNotNone(main_win_match)
        assert main_win_match is not None
        self.assertNotIn("generation", main_win_match.group(1).lower())

        for attr_val in re.findall(r'="([^"]*)"', html_txt):
            self.assertNotIn(">", attr_val, f"Literal '>' found inside HTML attribute value: {attr_val!r}")

        ag_css = css_txt.split("VIEW 4B: ACTION GRAPH", 1)[1]
        for rem_str in re.findall(r"font-size:\s*([0-9.]+)rem", ag_css):
            self.assertGreaterEqual(float(rem_str), 0.86, f"Font size {rem_str}rem < 0.86rem floor")
        self.assertIn(".page-view.ag-view", css_txt)
        self.assertIn(".ag-edge-path", css_txt)
        self.assertIn(".ag-node-card", css_txt)

        forbidden_non_english = (
            "grafo",
            "ações",
            "acoes",
            "parâmetros",
            "parametros",
            "simular",
            "aplicar",
            "configuração",
            "configuracao",
            "heurística",
            "algoritmo",
            "dependências",
        )
        combined_low = (html_txt + "\n" + js_txt).lower()
        for word in forbidden_non_english:
            self.assertNotIn(word, combined_low, f"Non-English token {word!r} found in UI")

    def test_node_vm_action_graph_render_mode_switch_simulation_poll_guard_reset_and_apply(self) -> None:
        from spaceinvasion.config import load_strategy_config

        self.assertIsNotNone(shutil.which("node"), "Node.js is required")
        bridge, server, base_url = self._start_server(planner_mode="priority")
        try:
            fetch_patch_js = r"""
async (url, opts, fullUrl, nativeFetch) => {
  if (url === "/command" && opts && opts.body) {
    const b = JSON.parse(String(opts.body));
    if (b.command === "action_graph") {
      if (globalThis.__agHookMode === "slow_ga" && b.planner_mode === "ga_nn") {
        await new Promise((r) => setTimeout(r, 60));
      } else if (globalThis.__agHookMode === "fail_500") {
        return {
          ok: false,
          status: 500,
          json: async () => ({ ok: false, error: "Injected 500 simulation error" }),
        };
      }
    }
  }
}
"""
            scenario_js = r"""
await __waitUntil(() => state.dashboard !== null);

const navBtn = document.querySelector('[data-page="action-graph"]');
navBtn.click();
const viewVisible = !el("view-action-graph").hidden && el("view-dashboard").hidden;
const initialNodes = document.querySelectorAll(".ag-node-card").length;
const svgEl = el("ag-edges-svg");
const initialPaths = document.querySelectorAll(".ag-edge-path").length;
const mainWinHasGen = el("ag-main-window").textContent.toLowerCase().includes("generation");
const initialStatusPill = el("ag-sim-status-pill").textContent;

const firstCard = document.querySelector(".ag-node-card");
if (firstCard) firstCard.click();
const inspStepAfterClick = el("ag-insp-step").textContent;
const inspTitleAfterClick = el("ag-insp-title").textContent;

document.querySelector('[data-ag-edge-filter="deps"]').click();
const depsFilterActive = document.querySelector('[data-ag-edge-filter="deps"]').classList.contains("active");
document.querySelector('[data-ag-edge-filter="all"]').click();

el("ag-mode-ga_nn").click();
await __waitUntil(() => state.agSimRunning === 0 && state.actionGraphData?.mode === "ga_nn");
const simModeStatusPill = el("ag-sim-status-pill").textContent;
const simDirtyBadge = el("ag-dirty-badge").textContent;
const simGaNodes = document.querySelectorAll(".ag-node-card").length;
const mainWinHasGenInGa = el("ag-main-window").textContent.toLowerCase().includes("generation");
const updateCallsDuringSim = fetchLog.filter((f) => f.url === "/command" && f.body?.command === "update_config").length;

// 2a. Toggling "priority" -> "ga_nn" -> "priority" resets state.agSimDirty to false and restores LIVE ACTIVE CONFIG
el("ag-mode-priority").click();
await __waitUntil(() => state.agSimRunning === 0 && state.actionGraphData?.mode === "priority");
const dirtyAfterRoundtripPrio = state.agSimDirty;
const statusPillAfterRoundtripPrio = el("ag-sim-status-pill").textContent;
const dirtyBadgeAfterRoundtripPrio = el("ag-dirty-badge").textContent;
const engineSubAfterRoundtripPrio = el("ag-kpi-engine-sub").textContent;

// Editing an input and reverting it back to active_config value resets state.agSimDirty to false
const origPlaystyle = el("ag-cfg-playstyle").value;
el("ag-cfg-playstyle").value = "miner";
el("ag-cfg-playstyle").dispatchEvent("change");
await __waitUntil(() => state.agSimRunning === 0 && state.agSimDirty === true);
const dirtyAfterInputEdit = state.agSimDirty;
el("ag-cfg-playstyle").value = origPlaystyle;
el("ag-cfg-playstyle").dispatchEvent("change");
await __waitUntil(() => state.agSimRunning === 0 && state.agSimDirty === false);
const dirtyAfterInputRevert = state.agSimDirty;
const statusPillAfterInputRevert = el("ag-sim-status-pill").textContent;

// 2b. Rapid overlapping "ga_nn" <-> "priority" switches & HTTP 400 / 500 errors keep state.gaCalcCount === 0
globalThis.__agHookMode = "slow_ga";
el("ag-mode-ga_nn").click();
const gaCalcDuringSlowGa = state.gaCalcCount;
el("ag-mode-priority").click();
el("ag-mode-ga_nn").click();
el("ag-mode-priority").click();
await __waitUntil(() => state.agSimRunning === 0 && state.gaCalcCount === 0 && state.actionGraphData?.mode === "priority");
const gaCalcAfterOverlap = state.gaCalcCount;
const hudCalcAfterOverlap = el("gann-calc-hud").classList.contains("is-calculating");
const dirtyAfterOverlapPrio = state.agSimDirty;
globalThis.__agHookMode = null;

// Simulated HTTP 400 validation error in "ga_nn" mode
el("ag-cfg-planner_mode").value = "ga_nn";
el("ag-cfg-ga_seed").value = "bad_seed_400";
await runAgSimulation({ explicit: true });
const gaCalcAfter400 = state.gaCalcCount;
const bannerAfter400 = el("feedback-banner").textContent;

// Simulated HTTP 500 command error in "ga_nn" mode
el("ag-cfg-ga_seed").value = "42";
globalThis.__agHookMode = "fail_500";
await runAgSimulation({ explicit: true });
const gaCalcAfter500 = state.gaCalcCount;
const bannerAfter500 = el("feedback-banner").textContent;
globalThis.__agHookMode = null;

el("ag-cfg-planner_mode").value = "ga_nn";
el("ag-cfg-ga_population_size").value = "8";
el("ag-cfg-ga_generations").value = "2";
el("ag-cfg-ga_horizon_steps").value = "4";
el("ag-cfg-ga_seed").value = "33";
el("ag-cfg-playstyle").value = "miner";
el("ag-ptab-btn-scope").click();
const scopePaneVisible = !el("ag-param-pane-scope").hidden;
el("ag-cfg-allow_raids").checked = true;
el("btn-ag-simulate").click();
await __waitUntil(() => state.agSimRunning === 0 && el("feedback-banner").textContent.includes("Simulated ga_nn action graph"));

await loadDashboard({ refresh: false, preserveConfig: true, force: true });
const popAfterPoll = el("ag-cfg-ga_population_size").value;
const playAfterPoll = el("ag-cfg-playstyle").value;
const modeAfterPoll = state.actionGraphData?.mode;
const activeDashModeDuringSim = state.dashboard?.config?.planner_mode;

el("btn-ag-reset").click();
await __waitUntil(() => !state.agSimDirty && state.actionGraphData?.mode === "priority");
const statusAfterReset = el("ag-sim-status-pill").textContent;
const modeSelectAfterReset = el("ag-cfg-planner_mode").value;

el("ag-ptab-btn-core").click();
el("ag-cfg-planner_mode").value = "ga_nn";
el("ag-cfg-ga_population_size").value = "8";
el("ag-cfg-ga_generations").value = "2";
el("ag-cfg-ga_horizon_steps").value = "4";
el("ag-cfg-ga_seed").value = "55";
el("ag-cfg-playstyle").value = "hybrid";
el("btn-ag-apply").click();
await __waitUntil(() => state.configSaving === 0 && !state.agSimDirty && state.dashboard?.config?.planner_mode === "ga_nn" && state.dashboard?.config?.ga_seed === 55);

const fullText = el("view-action-graph").textContent;
return {
  viewVisible,
  initialNodes,
  hasSvg: Boolean(svgEl),
  initialPaths,
  mainWinHasGen,
  mainWinHasGenInGa,
  initialStatusPill,
  inspStepAfterClick,
  inspTitleAfterClick,
  depsFilterActive,
  simModeStatusPill,
  simDirtyBadge,
  simGaNodes,
  updateCallsDuringSim,
  dirtyAfterRoundtripPrio,
  statusPillAfterRoundtripPrio,
  dirtyBadgeAfterRoundtripPrio,
  engineSubAfterRoundtripPrio,
  dirtyAfterInputEdit,
  dirtyAfterInputRevert,
  statusPillAfterInputRevert,
  gaCalcDuringSlowGa,
  gaCalcAfterOverlap,
  hudCalcAfterOverlap,
  dirtyAfterOverlapPrio,
  gaCalcAfter400,
  bannerAfter400,
  gaCalcAfter500,
  bannerAfter500,
  scopePaneVisible,
  popAfterPoll,
  playAfterPoll,
  modeAfterPoll,
  activeDashModeDuringSim,
  statusAfterReset,
  modeSelectAfterReset,
  appliedDashMode: state.dashboard?.config?.planner_mode,
  appliedPlaystyle: state.dashboard?.config?.playstyle,
  appliedCfgFormMode: el("cfg-planner_mode").value,
  dashBadgeAfterApply: el("dash-planner-mode-badge").textContent,
  navGraphBadgeAfterApply: el("nav-graph-badge").textContent,
  applyBanner: el("feedback-banner").textContent,
  hasUndefinedOrNan: /\b(undefined|NaN|Infinity)\b/.test(fullText),
};
"""
            with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                res = _run_node_ui(base_url, scenario_js, fetch_patch_js=fetch_patch_js)
            self.assertEqual(res["errors"], [])
            sc = res["scenario"]
            self.assertTrue(sc["viewVisible"])
            self.assertGreater(sc["initialNodes"], 0)
            self.assertTrue(sc["hasSvg"])
            self.assertFalse(sc["mainWinHasGen"])
            self.assertFalse(sc["mainWinHasGenInGa"])
            self.assertEqual(sc["initialStatusPill"], "LIVE ACTIVE CONFIG")
            self.assertEqual(sc["inspStepAfterClick"], "#1")
            self.assertTrue(sc["depsFilterActive"])
            self.assertEqual(sc["simModeStatusPill"], "SIMULATION PREVIEW · UNSAVED")
            self.assertEqual(sc["simDirtyBadge"], "MODIFIED (PREVIEW)")
            self.assertGreater(sc["simGaNodes"], 0)
            self.assertEqual(sc["updateCallsDuringSim"], 0)
            self.assertFalse(sc["dirtyAfterRoundtripPrio"])
            self.assertEqual(sc["statusPillAfterRoundtripPrio"], "LIVE ACTIVE CONFIG")
            self.assertEqual(sc["dirtyBadgeAfterRoundtripPrio"], "ACTIVE SYNCED")
            self.assertIn("Active ·", sc["engineSubAfterRoundtripPrio"])
            self.assertTrue(sc["dirtyAfterInputEdit"])
            self.assertFalse(sc["dirtyAfterInputRevert"])
            self.assertEqual(sc["statusPillAfterInputRevert"], "LIVE ACTIVE CONFIG")
            self.assertGreaterEqual(sc["gaCalcDuringSlowGa"], 1)
            self.assertEqual(sc["gaCalcAfterOverlap"], 0)
            self.assertFalse(sc["hudCalcAfterOverlap"])
            self.assertFalse(sc["dirtyAfterOverlapPrio"])
            self.assertEqual(sc["gaCalcAfter400"], 0)
            self.assertIn("Simulation preview error", sc["bannerAfter400"])
            self.assertEqual(sc["gaCalcAfter500"], 0)
            self.assertIn("Simulation preview error", sc["bannerAfter500"])
            self.assertTrue(sc["scopePaneVisible"])
            self.assertEqual(sc["popAfterPoll"], "8")
            self.assertEqual(sc["playAfterPoll"], "miner")
            self.assertEqual(sc["modeAfterPoll"], "ga_nn")
            self.assertEqual(sc["activeDashModeDuringSim"], "priority")
            self.assertEqual(sc["statusAfterReset"], "LIVE ACTIVE CONFIG")
            self.assertEqual(sc["modeSelectAfterReset"], "priority")
            self.assertEqual(sc["appliedDashMode"], "ga_nn")
            self.assertEqual(sc["appliedPlaystyle"], "hybrid")
            self.assertEqual(sc["appliedCfgFormMode"], "ga_nn")
            self.assertEqual(sc["dashBadgeAfterApply"], "MODE: GA+NN")
            self.assertEqual(sc["navGraphBadgeAfterApply"], "GA")
            self.assertIn("Applied parameters to active StrategyConfig", sc["applyBanner"])
            self.assertFalse(sc["hasUndefinedOrNan"])

            persisted = load_strategy_config(bridge.config_path)
            self.assertEqual(persisted.planner_mode, "ga_nn")
            self.assertEqual(persisted.playstyle, "hybrid")
            self.assertEqual(persisted.ga_seed, 55)
        finally:
            bridge.close()
            server.shutdown()
            server.server_close()
            self._tmp.cleanup()

    def test_node_vm_demolish_building_sim_dispatch_shows_clean_feedback_banner(self) -> None:
        self.assertIsNotNone(shutil.which("node"), "Node.js is required")
        planet = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=31,
            fields_used=16,
            fields_max=230,
            resources=Resources(5500, 900, 3100, 200),
            storage=Resources(100000, 100000, 100000, 100000),
            energy=EnergyBalance(500, 120),
            buildings={"b1": 3, "b3": 3, "b5": 8, "b6": 2},
            building_names={
                "b1": "Pig iron mine",
                "b3": "Kryptonite mine",
                "b5": "Fusion plant",
                "b6": "Thermosolar plant",
            },
            building_offers=_sample_state().planets[0].building_offers,
            economy_rates={"resource1": 80},
        )
        dem_state = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1234.5,
            planets=(planet,),
            active_planet_id=12476,
            researches={"r1": 1, "r2": 4},
            research_names={"r1": "Mining technology", "r2": "Energy technology"},
            speedup_factor=10.0,
        )
        with tempfile.TemporaryDirectory(prefix="si_ui_dem_") as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            cfg_path.write_text("planner_mode: priority\nallow_build: true\n", encoding="utf-8")
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = dem_state
            fake_client.navigate_menu.return_value = "<html>ok</html>"
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "s.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            bridge._economy_rates[12476] = {"resource1": 80}
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                scenario_js = r"""
await __waitUntil(() => state.dashboard !== null && Array.isArray(state.dashboard.plan));

const demIdx = state.dashboard.plan.findIndex((a) => a.kind === "DEMOLISH_BUILDING");
if (demIdx < 0) {
  throw new Error("Expected planned DEMOLISH_BUILDING action in dashboard.plan");
}
const planRows = el("plan-tbody").querySelectorAll("tr");
const demRow = planRows[demIdx];
const simBtn = Array.from(demRow.querySelectorAll("button")).find((b) => b.textContent.trim() === "Sim");
if (!simBtn) {
  throw new Error("Expected Sim button on executable DEMOLISH_BUILDING row");
}
simBtn.click();
await __waitUntil(() =>
  fetchLog.some((f) => f.url === "/command" && f.body?.command === "demolish_building") &&
  el("feedback-banner").textContent.includes("DEMOLISH_BUILDING")
);

const banner = el("feedback-banner");
const cmdEntry = fetchLog.find((f) => f.url === "/command" && f.body?.command === "demolish_building");
return {
  demActionKind: state.dashboard.plan[demIdx].kind,
  cmdBody: cmdEntry ? cmdEntry.body : null,
  bannerClass: banner.className,
  bannerHasError: banner.classList.contains("is-error"),
  bannerText: banner.textContent,
};
"""
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    res = _run_node_ui(base_url, scenario_js)
                self.assertEqual(res["errors"], [])
                sc = res["scenario"]
                self.assertEqual(sc["demActionKind"], "DEMOLISH_BUILDING")
                self.assertEqual(
                    sc["cmdBody"],
                    {
                        "command": "demolish_building",
                        "building_id": 6,
                        "planet_id": 12476,
                        "dry_run": True,
                    },
                )
                self.assertFalse(sc["bannerHasError"], sc)
                self.assertNotIn("is-error", sc["bannerClass"])
                self.assertIn("Would execute DEMOLISH_BUILDING: Thermosolar plant -> L1", sc["bannerText"])
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()


    def test_node_vm_espionage_colonies_attacks_and_recyclers_views(self) -> None:
        import time
        from spaceinvasion.fleet import GalaxySlot, InboxMessage, SpyReport

        p_home = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=-5,
            fields_used=126,
            fields_max=230,
            resources=Resources(90000, 65000, 45000, 25000),
            storage=Resources(500000, 500000, 500000, 500000),
            energy=EnergyBalance(5500, 1500),
            buildings={"b1": 19, "b2": 18, "b5": 21, "b6": 10, "b13": 6},
            ships={"s1": 12, "s8": 1, "s10": 11, "s11": 4},
            building_offers=_sample_state().planets[0].building_offers,
        )
        st = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1700000100.0,
            planets=(p_home,),
            active_planet_id=12476,
            researches={"r1": 4, "r2": 8, "r3": 6, "r6": 3},
            speedup_factor=10.0,
        )
        with tempfile.TemporaryDirectory(prefix="si_ui_intel_") as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            cfg_path.write_text(
                "planner_mode: priority\nallow_build: true\nforbid_planetary_solar: true\nallow_colonize: true\nmin_colony_fields: 300\n",
                encoding="utf-8",
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "s.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            assert bridge._db is not None
            bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
            bridge._last_snapshot_at = time.monotonic()

            slots = [
                GalaxySlot("02:380:05", 5, name="DebrisTarget", owner="Pirate", status_color="yellow", has_debris=True),
                GalaxySlot("02:380:09", 9, status_color="free"),
                GalaxySlot("02:380:10", 10, status_color="free"),
                GalaxySlot("02:378:15", 15, name="Colony", owner="Rival", status_color="white"),
            ]
            bridge._db.record_galaxy_scan(2, 380, slots[:3], ts=1700000050.0)
            bridge._db.record_galaxy_slots(slots, ts=1700000050.0)
            bridge._db.record_spy_report(
                SpyReport(target_coords="02:380:09", owner="", resources=Resources(), uninhabited_fields=330),
                status_color="free",
                ts=1700000060.0,
            )
            bridge._db.record_spy_report(
                SpyReport(
                    target_coords="02:383:02",
                    owner="Spacia",
                    planet_name="Spacia",
                    is_spacia=True,
                    resources=Resources(60000, 40000, 30000, 20000),
                ),
                ts=1700000070.0,
            )
            bridge._db.record_game_messages(
                [
                    InboxMessage(
                        msg_uid="uid:88001",
                        server_msg_id="88001",
                        folder_type="battle",
                        category="battle_report",
                        sender="System",
                        subject="Battle report Spacia [02:383:02]",
                        msg_date_text="11/16/2026 19:00:00",
                        coords="02:383:02",
                        body_text="Battle report #16254211 Winner: Attacker",
                        parsed_data={
                            "outcome": "win",
                            "rounds": 1,
                            "defender": "Spacia",
                            "loot": {"pig_iron": 3075, "metal": 2250, "kryptonite": 5565, "spice": 2550},
                            "loot_spice_eq": 5842.5,
                            "debris": {"pig_iron": 0, "metal": 0},
                        },
                    ),
                    InboxMessage(
                        msg_uid="uid:88002",
                        server_msg_id="88002",
                        folder_type="fleet",
                        category="recycle_report",
                        sender="System",
                        subject="Space rubble [02:380:05]",
                        msg_date_text="11/16/2026 19:05:00",
                        coords="02:380:05",
                        body_text="You have processed 17036 Pig iron, 12036 Metal, 14310 Kryptonite, 18098 Spice pieces of rubble.",
                        parsed_data={
                            "recycler_count": 3,
                            "cargo_capacity": 60000,
                            "harvested": {"pig_iron": 17036, "metal": 12036, "kryptonite": 14310, "spice": 18098},
                            "harvested_spice_eq": 30521.1,
                            "debris_available": {"pig_iron": 17036, "metal": 12036, "kryptonite": 14310, "spice": 18098},
                        },
                    ),
                    InboxMessage(
                        msg_uid="uid:88003",
                        server_msg_id="88003",
                        folder_type="fleet",
                        category="fleet_return",
                        sender="System",
                        subject="Colony [02:380:12] fleet return",
                        msg_date_text="11/16/2026 19:10:00",
                        coords="02:380:12",
                        body_text="A fleet returning from duty reaches Colony [02:380:12].",
                        parsed_data={"unloaded": {"pig_iron": 17036, "metal": 12036, "kryptonite": 14310, "spice": 18098}},
                    ),
                ]
            )
            bridge._db.record_target_block("02:378:15", blocked_until_ts=time.time() + 3600.0, reason="bash protection", is_bash=True)

            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                scenario_js = r"""
await __waitUntil(() => state.dashboard !== null && Array.isArray(state.dashboard.plan));

document.querySelector('[data-page="espionage"]').click();
await __waitUntil(() => state.espData !== null && el("esp-targets-tbody").textContent.includes("02:383:02"));
const espTxt = el("view-espionage").textContent;

document.querySelector('[data-page="colonies"]').click();
await __waitUntil(() => state.colonyData !== null && el("col-candidates-tbody").textContent.includes("330 fields"));
const colTxt = el("view-colonies").textContent;

document.querySelector('[data-page="attacks"]').click();
await __waitUntil(() => state.atkData !== null && el("atk-battles-tbody").textContent.includes("02:383:02"));
const atkTxt = el("view-attacks").textContent;

document.querySelector('[data-page="recyclers"]').click();
await __waitUntil(() => state.recData !== null && el("rec-harvests-tbody").textContent.includes("02:380:05"));
const recTxt = el("view-recyclers").textContent;

return {
  forbidChecked: Boolean(el("cfg-forbid_planetary_solar")?.checked),
  espTxt,
  colTxt,
  atkTxt,
  recTxt,
};
"""
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    res = _run_node_ui(base_url, scenario_js)
                self.assertEqual(res["errors"], [])
                sc = res["scenario"]
                self.assertTrue(sc["forbidChecked"])
                for key in ("espTxt", "colTxt", "atkTxt", "recTxt"):
                    self.assertNotIn("NaN", sc[key])
                    self.assertNotIn("undefined", sc[key])
                self.assertIn("SPACIA", sc["espTxt"])
                self.assertIn("330 fields", sc["colTxt"])
                self.assertIn("Unprobed", sc["colTxt"])
                self.assertIn("WIN", sc["atkTxt"])
                self.assertIn("02:378:15", sc["atkTxt"])
                self.assertIn("14,310 Kry", sc["recTxt"])
                self.assertIn("fleet_return", sc["recTxt"])
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()


    def test_node_vm_manual_queue_composer_pin_reprioritize_reorder_and_drop(self) -> None:
        import time

        p_home = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=15,
            fields_used=55,
            fields_max=230,
            resources=Resources(95_000, 80_000, 65_000, 45_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(2200, 900),
            buildings={"b1": 10, "b2": 9, "b3": 8, "b4": 7, "b5": 15, "b7": 5, "b8": 4, "b9": 4},
            ships={"s1": 6, "s8": 1, "s10": 8, "s11": 2},
            building_offers=_sample_state().planets[0].building_offers,
        )
        st = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1700000600.0,
            planets=(p_home,),
            active_planet_id=12476,
            researches={"r1": 3, "r2": 6, "r6": 3},
            speedup_factor=10.0,
        )
        with tempfile.TemporaryDirectory(prefix="si_ui_mq_") as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            cfg_path.write_text("planner_mode: priority\nallow_build: true\n", encoding="utf-8")
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "mq.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
            bridge._last_snapshot_at = time.monotonic()

            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                scenario_js = r"""
await __waitUntil(() => state.dashboard !== null && Array.isArray(state.dashboard.plan));

// 1. Click "+ Queue Command" from Dashboard -> jumps to Operations & populates composer
el("btn-dash-add-queue").click();
await __waitUntil(() => !el("view-operations").hidden && el("mq-target-id").children.length > 0);

// 2. Submit a P1 BUILD Spice mine (b4) -> L8 via the composer form
el("mq-kind").value = "BUILD";
el("mq-kind").dispatchEvent({ type: "change" });
el("mq-target-id").value = "4";
el("mq-target-id").dispatchEvent({ type: "change" });
el("mq-level").value = "8";
el("mq-priority").value = "P1";
el("mq-note").value = "Priority Spice";
el("mq-composer-form").dispatchEvent({ type: "submit", preventDefault() {} });
await __waitUntil(() => (state.dashboard?.manual_queue || []).length === 1);

// 3. Submit a second P1 SPY command at bottom -> now 2 pinned items
el("mq-kind").value = "SPY";
el("mq-kind").dispatchEvent({ type: "change" });
el("mq-coords").value = "02:381:12";
el("mq-level").value = "1";
el("mq-priority").value = "P1";
el("mq-position").value = "bottom";
el("mq-composer-form").dispatchEvent({ type: "submit", preventDefault() {} });
await __waitUntil(() => (state.dashboard?.manual_queue || []).length === 2);

const beforeMove = state.dashboard.manual_queue.map((x) => x.kind);

// 4. Click "Up" on the second pinned row (SPY) -> moves SPY above BUILD!
const manualRows = Array.from(el("plan-tbody").querySelectorAll("tr.is-manual-queued"));
const secondRowUpBtn = Array.from(manualRows[1].querySelectorAll("button")).find((b) => b.textContent.trim() === "Up");
secondRowUpBtn.click();
await __waitUntil(() => (state.dashboard?.manual_queue || [])[0]?.kind === "SPY");
const afterMove = state.dashboard.manual_queue.map((x) => x.kind);

// 5. Change first row's priority dropdown to P0
const firstPrioSel = el("plan-tbody").querySelector("tr.is-manual-queued select.mq-prio-select");
firstPrioSel.value = "P0";
firstPrioSel.dispatchEvent({ type: "change" });
await __waitUntil(() => (state.dashboard?.manual_queue || [])[0]?.priority === "P0");

// 6. Filter to "Pinned Only" chip and check row count = 2, then Drop 1 item
el("chip-filter-manual").click();
const pinnedFilteredRows = el("plan-tbody").querySelectorAll("tr.is-manual-queued").length;
const dropBtn = Array.from(el("plan-tbody").querySelectorAll("tr.is-manual-queued button")).find((b) => b.textContent.trim() === "Drop");
dropBtn.click();
await __waitUntil(() => (state.dashboard?.manual_queue || []).length === 1);

return {
  beforeMove,
  afterMove,
  topPrio: state.dashboard.manual_queue[0].priority,
  pinnedFilteredRows,
  remainingCount: state.dashboard.manual_queue.length,
  opsText: el("view-operations").textContent,
};
"""
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    res = _run_node_ui(base_url, scenario_js)
                self.assertEqual(res["errors"], [])
                sc = res["scenario"]
                self.assertEqual(sc["beforeMove"], ["BUILD", "SPY"])
                self.assertEqual(sc["afterMove"], ["SPY", "BUILD"])
                self.assertEqual(sc["pinnedFilteredRows"], 2)
                self.assertEqual(sc["remainingCount"], 1)
                self.assertNotIn("NaN", sc["opsText"])
                self.assertNotIn("undefined", sc["opsText"])
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()


    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_dashboard_removes_building_eta_card_and_splits_three_side_by_side_queues(self) -> None:
        import time

        p_home = PlanetState(
            planet_id=12476,
            name="Colony",
            coords="02:380:12",
            planet_type="normal",
            temperature=15,
            fields_used=55,
            fields_max=230,
            resources=Resources(95_000, 80_000, 65_000, 45_000),
            storage=Resources(500_000, 500_000, 500_000, 500_000),
            energy=EnergyBalance(2200, 900),
            buildings={"b1": 10, "b2": 9, "b3": 8, "b4": 7, "b5": 15, "b7": 5, "b8": 4, "b9": 4},
            ships={"s1": 6, "s8": 1, "s10": 8, "s11": 2},
            building_offers=_sample_state().planets[0].building_offers,
        )
        st = GameState(
            universe_id=31,
            user_id=103603,
            nickname="ventkx86",
            timestamp=1700000700.0,
            planets=(p_home,),
            active_planet_id=12476,
            researches={"r1": 3, "r2": 6, "r6": 3},
            speedup_factor=10.0,
        )
        with tempfile.TemporaryDirectory(prefix="si_ui_3q_") as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            cfg_path.write_text("planner_mode: priority\nallow_build: true\n", encoding="utf-8")
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st
            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                config_path=cfg_path,
                db_path=td_path / "three_q.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
            bridge._last_snapshot_at = time.monotonic()

            # Enqueue 1 Building, 1 Fleet (Ship), and 1 Research command so all 3 Dashboard queues populate
            bridge._db.enqueue_manual_command(
                kind="BUILD", planet_id=12476, planet_coords="02:380:12", target_id=4, target_name="Spice mine", target_level=8, priority="P1"
            )
            bridge._db.enqueue_manual_command(
                kind="BUILD_SHIP", planet_id=12476, planet_coords="02:380:12", target_id=10, target_name="Spy probe", target_level=4, priority="P1"
            )
            bridge._db.enqueue_manual_command(
                kind="RESEARCH", planet_id=12476, planet_coords="02:380:12", target_id=1, target_name="Mining technology", target_level=4, priority="P1"
            )

            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                html_txt = requests.get(f"{base_url}/", timeout=5).text
                css_txt = requests.get(f"{base_url}/styles.css", timeout=5).text
                self.assertNotIn('id="dash-eta-tbody"', html_txt)
                self.assertNotIn("Nearest Building Reach Times", html_txt)
                self.assertIn('id="eta-table-tbody"', html_txt)
                self.assertRegex(
                    css_txt,
                    r"\.dash-lower-grid\s*\{[^}]*grid-template-columns\s*:\s*repeat\(\s*3\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\s*\)",
                )

                scenario_js = r"""
await __waitUntil(() => state.dashboard !== null && Array.isArray(state.dashboard.plan));
const dashView = el("view-dashboard");
const cards = Array.from(dashView.querySelectorAll(".dash-queue-card")).map((c) => c.id);
const bldTxt = el("dash-plan-tbody").textContent;
const fltTxt = el("dash-plan-fleet-tbody").textContent;
const resTxt = el("dash-plan-research-tbody").textContent;
const bldCount = Number(el("dash-bld-q-count").textContent);
const fltCount = Number(el("dash-fleet-q-count").textContent);
const resCount = Number(el("dash-research-q-count").textContent);

// Click "Research ->" button on Research Queue card -> jumps to Buildings view filtered to research
el("btn-dash-goto-research").click();
const onBldView = !el("view-buildings").hidden;
const catAfterJump = state.bldCategory;

// Click "+ Queue" on Fleet Queue card -> jumps to Operations with BUILD_SHIP selected
el("btn-dash-add-fleet-queue").click();
const mqKindAfterFleetJump = el("mq-kind").value;

return {
  hasDashEta: Boolean(el("dash-eta-tbody")),
  cards,
  bldTxt,
  fltTxt,
  resTxt,
  bldCount,
  fltCount,
  resCount,
  onBldView,
  catAfterJump,
  mqKindAfterFleetJump,
};
"""
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    res = _run_node_ui(base_url, scenario_js)
                self.assertEqual(res["errors"], [])
                sc = res["scenario"]
                self.assertFalse(sc["hasDashEta"])
                self.assertEqual(
                    sc["cards"],
                    ["dash-queue-card-building", "dash-queue-card-fleet", "dash-queue-card-research"],
                )
                self.assertGreaterEqual(sc["bldCount"], 1)
                self.assertGreaterEqual(sc["fltCount"], 1)
                self.assertGreaterEqual(sc["resCount"], 1)
                self.assertIn("Spice mine", sc["bldTxt"])
                self.assertNotIn("Spy probe", sc["bldTxt"])
                self.assertNotIn("Mining technology", sc["bldTxt"])
                self.assertIn("Spy probe", sc["fltTxt"])
                self.assertNotIn("Spice mine", sc["fltTxt"])
                self.assertIn("Mining technology", sc["resTxt"])
                self.assertNotIn("Spice mine", sc["resTxt"])
                self.assertTrue(sc["onBldView"])
                self.assertEqual(sc["catAfterJump"], "research")
                self.assertEqual(sc["mqKindAfterFleetJump"], "BUILD_SHIP")
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_favicon_top_bar_notifications_and_auto_help_popup_persistence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="si_ui_notif_") as td:
            td_path = Path(td)
            p_home = PlanetState(
                planet_id=12476,
                name="Colony",
                coords="02:380:12",
                planet_type="normal",
                temperature=31,
                fields_used=45,
                fields_max=230,
                resources=Resources(50_000, 30_000, 40_000, 20_000),
                storage=Resources(500_000, 500_000, 500_000, 500_000),
                energy=EnergyBalance(1200, 600),
                buildings={"b1": 12, "b5": 14, "b9": 4, "b10": 5},
                ships={"s10": 6},
            )
            st = GameState(
                universe_id=31,
                user_id=103603,
                nickname="ventkx86",
                timestamp=1700000500.0,
                planets=(p_home,),
                active_planet_id=12476,
            )
            fake_client = MagicMock()
            fake_client.__enter__.return_value = fake_client
            fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
            fake_client.read_full_snapshot.return_value = st
            # Simulate Spy Step 1 -> Step 2 returning "This user can't be attacked!"
            fake_client.navigate_menu.return_value = (
                '<form action="indexInternal.es?action=internalSendFleet">'
                '<input name="ship_10" value="0"/><input name="gal"/><input name="sun"/><input name="pla"/>'
                '<input type="submit" value="Continue"/></form>'
            )
            fake_client._wait_frame_ready.return_value = (
                '<div>You have 2 unread Messages! [i]</div>'
                '<div style="color:red">This user can\'t be attacked!</div>'
            )

            bridge = GameApiBridge(
                debug_port=19999,
                env_file=td_path / "no.env",
                db_path=td_path / "notif.db",
                log_dir=None,
                client_factory=lambda _port: fake_client,
                ensure_session_fn=lambda: None,
            )
            bridge._record_state(st, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
            bridge._last_snapshot_at = 1e9
            server = create_api_server(bridge, host="127.0.0.1", port=0)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                fav_svg = requests.get(f"{base_url}/favicon.svg", timeout=5)
                self.assertEqual(fav_svg.status_code, 200)
                self.assertIn("rx=\"14\"", fav_svg.text)
                fav_ico = requests.get(f"{base_url}/favicon.ico", timeout=5)
                self.assertEqual(fav_ico.status_code, 200)
                self.assertGreater(len(fav_ico.content), 64)

                # Trigger live SPY to protected user -> records 6h target block + unread SQLite notification
                spy_res = requests.post(
                    f"{base_url}/command",
                    json={"command": "spy", "target_coords": "02:381:12", "planet_id": 12476, "dry_run": False},
                    timeout=5,
                ).json()
                self.assertFalse(spy_res["executed"])
                self.assertFalse(spy_res["validated"])
                self.assertIn("can't be attacked", spy_res["message"].lower())
                self.assertEqual(spy_res["notifications"]["unread_count"], 1)

                scenario_js = """
await __waitUntil(() => document.getElementById("notif-unread-badge") && !document.getElementById("notif-unread-badge").hidden);
const badgeBefore = document.getElementById("notif-unread-badge").textContent;
const helpAutoOpened = !document.getElementById("notif-help-modal").hidden;
const helpTitle = document.getElementById("notif-help-title").textContent;
const helpPrompt = document.getElementById("notif-help-prompt").textContent;

document.getElementById("btn-notifications").click();
await __waitUntil(() => !document.getElementById("notif-center-modal").hidden && document.getElementById("notif-center-list").textContent.toLowerCase().includes("can't be attacked"));
const centerText = document.getElementById("notif-center-list").textContent;

document.getElementById("btn-notif-mark-all").click();
await __waitUntil(() => document.getElementById("notif-unread-badge").hidden);
const badgeHiddenAfterAck = document.getElementById("notif-unread-badge").hidden;

return { badgeBefore, helpAutoOpened, helpTitle, helpPrompt, centerText, badgeHiddenAfterAck };
"""
                with patch("spaceinvasion.api_server.is_debug_port_open", return_value=True):
                    res = _run_node_ui(base_url, scenario_js)
                self.assertEqual(res["errors"], [])
                sc = res["scenario"]
                self.assertEqual(sc["badgeBefore"], "1")
                self.assertTrue(sc["helpAutoOpened"])
                self.assertIn("SPY", sc["helpTitle"])
                self.assertIn("6-hour target cooldown", sc["helpPrompt"])
                self.assertIn("can't be attacked", sc["centerText"].lower())
                self.assertTrue(sc["badgeHiddenAfterAck"])
            finally:
                server.shutdown()
                server.server_close()
                bridge.close()


if __name__ == "__main__":
    unittest.main()
