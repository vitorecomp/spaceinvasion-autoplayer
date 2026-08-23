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
    ui_dir = Path(__file__).resolve().parents[1] / "spaceinvasion" / "ui"
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


if __name__ == "__main__":
    unittest.main()
