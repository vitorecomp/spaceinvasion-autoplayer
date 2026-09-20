# 01 — Web Shell, Pages, DOM Selectors & Click Contracts

This document specifies how **SpaceInvasion Classic** works at the browser/HTTP boundary and defines the exact click, form, and read contracts required by the runtime.

## 1. Authentication & Three-Iframe Game Shell `[VERIFIED-LIVE]`

- **REQ-UI-001 (BitMeUp Portal Login)**: Authentication hits `https://spaceinvasion.bitmeup.com/?locale=en` (parsing dynamic `initPortalClient` tokens and `SI_TPL.startPortal` universe descriptors). Successful universe entry redirects into `indexGame.es?sid=<hex_session_token>`.
- **REQ-UI-002 (Strict Human Handoff)**: If Cloudflare Turnstile, CAPTCHA, or a 2FA prompt appears without `SPACEINVASION_2FA_CODE`, automation **MUST** abort immediately and let the operator resolve the challenge directly in Chrome (`127.0.0.1:9222`).
- **REQ-UI-003 (Three-Iframe Architecture)**: `indexGame.es` mounts three peer iframes inside top-level `default_content`:
  1. `F_Navi` (`indexInternal.es?action=internalMenu`) — persistent left navigation links & unread message counters.
  2. `F_planets` (`indexInternal.es?action=internalPlanetsOverview`) — embeds the empire JSON state inside `<script id="json-container">`.
  3. `F_Hauptframe` — active content viewport rendering the selected game screen.
- **REQ-UI-004 (Stale `F_planets` Frame)**: Completing an action inside `F_Hauptframe` does **not** automatically reload `F_planets`. Readers must execute `location.reload()` inside `F_planets` before reading `#json-container` for post-action telemetry.
- **REQ-UI-005 (Countdown Auto-Reload)**: `F_Hauptframe` automatically reloads when a build/research timer reaches `00:00:00`. Click handlers **MUST** retry once on `StaleElementReferenceException` after waiting for `document.readyState === "complete"`.
- **REQ-UI-006 (Per-Minute Rate Limit)**: Excessive requests trigger the HTML warning *"You've made more than page views at last minute"*. Navigation pacing (`0.9s–1.8s` jitter) and skipping unneeded pages (e.g., skipping `internalResearch` when `b8 == 0`) prevent tripping this limiter.

---

## 2. Navigation Menu (`F_Navi`) & Global Selectors `[VERIFIED-LIVE]`

- **REQ-UI-010 (Safe Menu Clicking)**: Links inside `F_Navi` can have adjacent `<td class="rahmen">` borders intercept webdriver coordinates (`ElementClickInterceptedException`). Menu navigation must `scrollIntoView({block: 'center'})`, attempt `.click()`, and fall back to JS `arguments[0].click()`.
- **REQ-UI-011 (Observed `F_Navi` Route Map)**:

| UI Label | Action Query / Trigger | Purpose | Status |
|---|---|---|---|
| Command central | `action=internalHome` | Tutorial box, active timers, fleet alerts, planet selector | `[VERIFIED-LIVE]` |
| Buildings | `action=internalBuildings` | Building upgrade cards (`b1`–`b21`) and build queue | `[VERIFIED-LIVE]` |
| Industry | `action=internalEconomy` | Production % sliders, Building Upgrades (`+3%`), Spice Transfer | `[VERIFIED-LIVE]` |
| Research | `action=internalResearch` | Global research cards (`researchID=<id>`) | `[VERIFIED-LIVE]` |
| Weapons factory | `action=internalWeaponFactory` | Ship construction form (`buildShip=1`) | `[VERIFIED-LIVE]` |
| Defence | `action=internalDefense` | Planetary defense form (`buildDefense=1`) | `[VERIFIED-LIVE]` |
| Fleet commands | `action=internalFleet` | Target selector & ship dispatch (`internalSendFleet`) | `[VERIFIED-LIVE]` |
| Galaxy | `href="#"` `onclick="p_galaxy()"` | Opens Galaxy Map (Classic/Modern/Mobile/3D) in viewport | `[VERIFIED-LIVE]` |
| Messages (tabs) | `action=internalMessages&type={normal,alliance,spio,battle,fleet,admin}` | Combat, espionage (`spio`), fleet & system reports | `[VERIFIED-LIVE]` |
| Interdimensional Transmitter | `action=internalTransmitter` | Free/Urplasma spin rewards | `[VERIFIED-LIVE]` |
| Alien | `action=internalAlien` | Weekly Alien wave fleet & Black Holes | `[VERIFIED-LIVE]` |
| Synth network | `action=internalSynth` | 346-node Synth Former / AI-Core skill tree | `[VERIFIED-LIVE]` |
| Specialization | `action=internalSpecs` | Miner / Fleeter / Researcher / Alien class perks | `[VERIFIED-LIVE]` |
| Overview and events | `action=internalUniverse` | Speed-Up multiplier table & AZ points table (`AZ 1–20`) | `[VERIFIED-LIVE]` |

- **REQ-UI-012 (Planet Switcher)**: Switching celestial bodies on `internalBuildings` / `internalResearch` / `internalWeaponFactory` selects `<option value="...newPlanet=<planet_id>">` inside `select[name="planet"]` and waits for `F_Hauptframe` reload.

---

## 3. Page-by-Page Read & Click Contracts

### 3.1 `F_planets` (`#json-container`) `[VERIFIED-LIVE]`
- **REQ-UI-020 (JSON Payload Fields)**:
  - `info.universe.id`, `info.user.{userID,nickname}`, `info.ts`.
  - `planets[]`: `{id, name, coords, type ("normal"|"raid"|"asteroid"|"event"), temperature, fields, resources:[pig,metal,kryp,spice], storage:[...], energy:{produced,used}, buildings:{"b1":{"level":N,"name":"..."}, ...}, ships:{"s1":...}, defense:{"d1":...}}`.
  - `researches`: `{"r1":{"level":N,"name":"..."}, ...}` (`r1`–`r15`).
  - `specs`: `{"miner":int,"fleeter":int,"researcher":int,"alien":int}`.

### 3.2 `internalHome` (Command Central & Tutorial) `[VERIFIED-LIVE]` / `[WIKI-SPEC]`
- **REQ-UI-030 (Tutorial State & Claim Click)**: `[VERIFIED-LIVE]`
  - Banner displays `Tutorial X / 11`. Task container `#tutorial.text` may be hidden by default (`display:none`), toggled via clicking `#tutorial.show`.
  - Building tasks follow `Increase your <Building Name> level <cur> / <target>`; research tasks follow `Upgrade your <Research Name> <cur> / <target>`. Counters are **relative increments within the step**, not absolute levels.
  - When all step tasks are met, `form[action*="internalHome"] input[name="tutorialComplete"] ~ input[type="submit"]` appears; clicking it claims the step reward and advances to step `X + 1`.
- **REQ-UI-031 (Summary Timers & Threats)**: `[VERIFIED-LIVE]`
  - Boxes `#sp-own-building`, `#sp-own-research`, and `#sp-own-fleet` summarize active queues/movements.
  - Hostile movement banners match `[class*="attack"], [id*="attack"]` (ignoring benign protection text).
- **REQ-UI-032 (Fleet Recall)**: `[WIKI-SPEC]`
  - Recalling a flight (including active Escape Flights) submits `POST` `recallFleet=<flight_id>` on `internalHome`. Normal recall returns in elapsed flight time; *Escape flight assistant+* halves return time.
- **REQ-UI-033 (Daily Login Bonus Popup & Homeworld Free Transmitter Flow)**: `[VERIFIED-LIVE]`
  - Opening Command Central (`action=internalHome`) triggers the Daily Login Bonus popup with a target-planet `<select id="tpid">` (which may sit inside a `display:none` wrapper on non-resource days), a defer link (`hideLoginBonus=1`, never click), and `onclick="sendDailyLogin();"` (`action=internalHome&getLoginBonus=1&tpid=<planet_id>&sid=...`).
  - Collecting the daily bonus grants the free Interdimensional Transmitter charge on `action=internalTransmitter`. Both `#tpid` and `internalTransmitter`'s `select[name="planet"]` (`newPlanet=<homeworld_id>`) must target the Pos. 1 Homeworld before activating `#tm.submit`.

### 3.3 `internalBuildings` `[VERIFIED-LIVE]`
- **REQ-UI-040 (Building Cards & Costs)**: Cards render inside `div.build-card.b-<id>` with detail link `a[href*="action=internalDetail&type=building&id=<id>"]`, cost text `Required: <Pig iron> <Metal> <Kryptonite> <Spice>` using dot-grouped thousands (`2.000` = `2000`), and build link `a.bau[href*="action=internalBuildItem&buildingID=<id>"]`.
- **REQ-UI-041 (Queue Box & Cancellation)**: Active and queued upgrades list under `Construction queue ( N Elements )` matching `<Building> (Level L)` with max **5 slots** (**6** with *Construction Commander*, **1** on *Asteroids*). Canceling refunds **70%** of resource cost.

### 3.4 `internalResearch` `[VERIFIED-LIVE]`
- **REQ-UI-050 (Research Cards & Start Click)**: Each technology row carries `a[href*="action=internalDetail&type=research&id=<id>"]`, `Required:` cost line, and `a.bau[href*="action=internalBuildItem&researchID=<id>"]` (*Research level N*).
- **REQ-UI-051 (Global Slot Rule)**: Only **1 research** runs globally across the entire account (+1 waiting slot with *Engineer* assistant). Cancellation refunds **70%**.

### 3.5 `internalWeaponFactory` & `internalDefense` `[VERIFIED-LIVE]` / `[WIKI-SPEC]`
- **REQ-UI-060 (Shipyard & Defense Forms)**: `[VERIFIED-LIVE]` Form targets `action="indexInternal.es?action=internalBuildItem&buildShip=1"` (or `buildDefense=1`) with hidden `sid` and pack count `packamount` (`1..10`, capped at **48 hours** per package; **70%** cancel refund).
- **REQ-UI-061 (Unit Order Inputs)**: `[WIKI-SPEC]` Unlocked unit cards link to `action=internalDetail&type={ship|defense}&id=<id>` and contain a numeric quantity `<input>` paired with form submit.

### 3.6 `internalEconomy` `[VERIFIED-LIVE]` / `[WIKI-SPEC]`
- **REQ-UI-070 (Mine Throttling)**: `[VERIFIED-LIVE]` Dropdown selects `resource1`..`resource5` throttle individual buildings (`100` down to `0`%) via `economy=1` submit (used to halt *Thermosolar plant* Spice burn or redirect power into *Teleporter*).
- **REQ-UI-071 (Global Building Upgrades)**: `[VERIFIED-LIVE]` Up to **5 upgrade slots** (`+3%` each, `+15%` max empire-wide) per *Pig iron mine*, *Blast furnace*, *Kryptonite mine*, *Spice mine*, *Fusion plant*, and *Quantum Array* (free AZ/VIP slots lock for 7 days once assigned; paid tiers use `buyUpgrade(name, id, days)` for 3/14/30 days).
- **REQ-UI-072 (Spice Transfer Tab)**: `[WIKI-SPEC]` Holds account-level banked reward Spice; paying out credits the currently selected planet immediately, subject to a daily payout cap and a **1-hour cooldown** between payouts (disabled on `[RK]` raid colonies and event worlds).

### 3.7 `internalFleet`, `Galaxy` (`p_galaxy()`), `Messages` (`type=spio`) `[VERIFIED-LIVE]` / `[WIKI-SPEC]`
- **REQ-UI-080 (Fleet Step 1 Form)**: `[VERIFIED-LIVE]` `form[action*="internalSendFleet"]` includes ship count fields, `All` toggle `javascript:setAllMax(1)`, target coordinates `gal`, `sun`, `pla`, asteroid checkbox `astro`, favourites `<select name="planet">`, and `continue` submit button.
- **REQ-UI-081 (Fleet Step 2 Dispatch)**: `[WIKI-SPEC]` Mission selector (`Attack`, `Transport`, `Station`, `Escape flight`, `Spying`, `Colonize`, `Recycle`, `Teleport`, `AKS`), speed `%` selector, intra-galaxy short vs. long route selector (useful for extending Escape Flight return times), cargo load inputs (reserving fuel Spice inside hold), and final launch button.
- **REQ-UI-082 (Galaxy & Scanner)**: `[VERIFIED-LIVE]` menu trigger `a[onclick*="p_galaxy()"]`; `[WIKI-SPEC]` renders `1..16` planetary positions per solar system with player color tags, `[A]` asteroid indicator, debris field marker, quick spy trigger, and Asteroid *Galaxy scanner* (`5,000 Spice/scan` on same galaxy within scanner range, showing all in-transit flights **except** Escape Flights and hidden Alien waves).
- **REQ-UI-083 (Espionage Messages)**: `[VERIFIED-LIVE]` tab `internalMessages&type=spio`; `[WIKI-SPEC]` reports resource stockpiles, buildings, fleet, defenses, `dug-in` status (with sufficient Spy tech lead), or inhabited/uninhabited ecology field estimates when probes scan empty coordinates.
