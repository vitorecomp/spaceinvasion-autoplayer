# 02 — Economy, Growth Formulas, Energy Grid & Storage

All baseline tables and helper calculations live in [../spaceinvasion/game_data.py](../spaceinvasion/game_data.py) and match the official SpaceInvasion Classic Wiki (`1x` normal universe speed, `RETRO`).

---

## 1. Resource Definitions & Coupling `[WIKI-SPEC]`

- **REQ-ECO-001 (Four Stored Resources)**:
  1. **Pig iron** (`b1` *Pig iron mine*): Base construction material and raw feedstock consumed by *Blast furnaces*.
  2. **Metal** (`b2` *Blast furnace*): Smelted **exclusively** from Pig iron inside *Blast furnaces* (never mined directly).
  3. **Kryptonite** (`b3` *Kryptonite mine*): Electronics/high-tech material required heavily for research, warships, and defenses.
  4. **Spice** (`b4` *Spice mine*): Rare fuel and high-tier material (fleet propellant, *Thermosolar* fuel, Galaxy scans `5,000`/scan, Synth/AI-Core runs).
- **REQ-ECO-002 (2:1 Blast Furnace Smelting Loss)**: *Blast furnace* (`b2`) consumes **exactly 2×** its hourly Metal output in Pig iron (`pig_iron_used = 2 * prod`). Upgrading `b2` beyond sustainable Pig iron supply collapses net Pig iron growth.
- **REQ-ECO-003 (Mining Technology Scope)**: *Mining technology* (`r1`) multiplies hourly output of `b1`, `b3`, and `b4` by **`+2%` per level (`1 + 0.02 * r1`)** across all planets simultaneously. **Metal output from `b2` is NOT boosted by Mining technology.**
- **REQ-ECO-004 (Merchant Exchange Parity)**: When the *Merchant* assistant is active on a planet, resources trade at the fixed parity:
  $$\text{Pig iron } 6 \;:\; \text{Metal } 2.5 \;:\; \text{Kryptonite } 3 \;:\; \text{Spice } 1$$
  Used by our planner (`spice_equivalent`) to normalize all build costs and ROI gains:
  $$\text{SpiceEq}(p, m, k, s) = \frac{p}{6} + \frac{m}{2.5} + \frac{k}{3} + s$$
  *Meta implication*: With active *Merchant*, veterans frequently stop upgrading `b2` after Tutorial steps and convert raw Pig iron at `6 : 2.5`, avoiding `b2`'s `2:1` loss, energy draw, and building fields (`merchant_mode: true`).

---

## 2. Scaling Formulas (`L` → `L+1`) `[WIKI-SPEC]`

Verified empirically across all scraped wiki level tables in [../spaceinvasion/game_data.py](../spaceinvasion/game_data.py):

| Entity Group | IDs | Cost Scaling (`L -> L+1`) | Build/Research Time Scaling |
|---|---|---|---|
| Mines & Power Plants | `b1`–`b6` | `base * ~1.51^(L - 1)` | Scales proportionally with resource cost (`~1.51x`) |
| Infrastructure & Warehouses | `b7`–`b19` | `base * 2.0^(L - 1)` (exact doubling) | Doubles per level (`2.0x`) |
| Tunnel System | `b20` | `base * 1.08^(L - 1)` | Scales `1.08x` per level |
| Quantum Array | `b21` | `base * 1.44^(L - 1)` | Scales `1.44x` per level |
| All Researches | `r1`–`r15` | `base * 2.0^(L - 1)` (exact doubling) | Doubles per level (`2.0x`) |

### Time Acceleration Formulas
- **REQ-ECO-010 (Development Center & Weapons Factory Divisors)**:
  - *Development center* (`b7` at level $L$): cuts planet building construction times by $\frac{1}{L + 1}$ (`L1 = 50%`, `L2 = 33%`, `L5 = 17%`, `L10 = 9.1%`, `L15 = 6.3%`).
  - *Microsystem accelerator* (`b17` at level $M$): halves remaining build times each level ($0.5^M$).
  - *Weapons factory* (`b9` at level $W$): divides ship and defense build times by $\frac{1}{W}$ (`L1 = 100%`, `L2 = 50%`, `L5 = 20%`, `L10 = 10%`).
  - **Universe Speed Multiplier** (`1x` RETRO, `1.5x` Sirius, `2x` GeNeSIS/Infinity, `3x` Nexus): multiplies **both** mine/furnace hourly output and build/research/shipyard speed inversely, **except Energy**, which does **not** scale with universe speed (`basics/universes`).

### Speed-Up Catch-Up System (`internalUniverse`) `[VERIFIED-LIVE]`
- **REQ-ECO-011 (RETRO Speed-Up Tiers)**: New/smaller accounts receive temporary speed multipliers relative to #1 account points:
  `<0.2%: 20x`, `<0.5%: 10x`, `<1%: 5x`, `<2%: 3x`, `<5%: 2x`, `<10%: 1.5x` (also grants account upgrade points as points rise).

---

## 3. Energy Grid, Temperature Curves & Warehouse Caps `[WIKI-SPEC]`

- **REQ-ECO-020 (Proportional Energy Throttling)**: Energy is instantaneous (not stored). When total demand exceeds supply (`energy.net < 0`), **all resource mines on that planet throttle proportionally** below `100%`. Planners must project `building_energy_delta` for pending builds and queue power ahead of mines whenever `projected_net < 0`.
- **REQ-ECO-021 (Energy Technology)**: `r2` increases total energy output across every planet by **`+1%` per level**.
- **REQ-ECO-022 (Power Sources Comparison)**:
  - **Fusion plant (`b5`)**: Fuel-free base power (`L1: +11`, `L5: +100`, `L10: +404`, `L15: +1,220`, `L20: +3,273`). Consumes 1 building field per level.
  - **Thermosolar plant (`b6`)**: Unlocked at `r2 L4` + `b4 L6`. Produces `+9/+80/+323/+2,618` at `L1/L5/L10/L20` while burning `2/20/80/654` **Spice/hour**. If Spice runs dry on-planet, power collapses to match incoming Spice production. Cost-effective mid-game supplement on cold high-Spice planets (`thermo_cpe < 0.70 * fusion_cpe`).
  - **Orbital Solar cells (`s9`)**: Built in *Weapons factory*; uses **0 building fields**. Output scales strictly linearly with warmth (`-160C: 10`, `0C: 30`, `+40C: 40`, `+120C: 60`, `+200C: 80` energy/unit). Vulnerable in orbital combat (partially auto-repaired post-battle).
- **REQ-ECO-023 (Surplus Energy Sinks)**: Excess unused energy charges *Particle cannon* (`b15`), *Particle shield* (`b16`), *Teleporter* (`b18`), and L40+ *Tunnel system* (`b20`).

### Temperature Curve: *Spice Mine* (`b4`) `[WIKI-SPEC]`
Production rises steadily on colder planets (linearly interpolated for intermediate planet temperatures):

| Level | `-120 °C` | `-80 °C` | `-40 °C` | `0 °C` | `+40 °C` | `+80 °C` | `+120 °C` | Energy Needed |
|---|---|---|---|---|---|---|---|---|
| **L1** | 8/h | 8/h | 7/h | 6/h | 6/h | 5/h | 5/h | -9 |
| **L3** | 34/h | 31/h | 29/h | 27/h | 25/h | 22/h | 20/h | -36 |
| **L5** | 75/h | 70/h | 65/h | 60/h | 55/h | 50/h | 45/h | -80 |
| **L10** | 303/h | 283/h | 262/h | 242/h | 222/h | 202/h | 182/h | -323 |
| **L15** | 915/h | 854/h | 793/h | 732/h | 671/h | 610/h | 549/h | -976 |
| **L20** | 2,454/h | 2,291/h | 2,127/h | 1,963/h | 1,800/h | 1,636/h | 1,472/h | -2,618 |

### Warehouse Capacity & Overflow Cap `[WIKI-SPEC]`
- **REQ-ECO-030 (Warehouse Scaling)**: Each planet starts with **100,000** base capacity per resource (`L0`). Upgrading `b11`–`b14` follows exact doubling:
  $$\text{Capacity}(L) = 100{,}000 \times 2^L$$
  (`L1 = 200,000`, `L2 = 400,000`, `L5 = 3,200,000`, `L10 = 102,400,000`, `L20 = 104,857,600,000`).
- **REQ-ECO-031 (5% Cap Penalty & 85% Guard)**: Once a stored resource reaches `100%` of warehouse capacity, that resource's mine production drops to **5%**. Planners trigger `P1` storage expansion or spending at `>= 85%` fill (`storage_warn_ratio: 0.85`).

---

## 4. Production Checkpoints (`1x` Speed, `Mining Tech = 0`, `0 °C`)

| Level | `b1` Pig Iron (/h, E) | `b2` Metal (/h, PigUsed, E) | `b3` Kryptonite (/h, E) | `b5` Fusion (+E) | `b6` Thermosolar (+E, SpiceUsed) |
|---|---|---|---|---|---|
| **L1** | 27 (`-4`) | 12 (`24`, `-2`) | 14 (`-5`) | `+11` | `+9` (`2/h`) |
| **L2** | 63 (`-10`) | 27 (`54`, `-5`) | 32 (`-11`) | `+26` | `+21` (`5/h`) |
| **L3** | 109 (`-18`) | 47 (`94`, `-9`) | 55 (`-20`) | `+45` | `+36` (`9/h`) |
| **L5** | 241 (`-40`) | 105 (`210`, `-20`) | 123 (`-45`) | `+100` | `+80` (`20/h`) |
| **L10** | 970 (`-161`) | 424 (`848`, `-80`) | 495 (`-182`) | `+404` | `+323` (`80/h`) |
| **L15** | 2,929 (`-488`) | 1,281 (`2,562`, `-244`) | 1,495 (`-549`) | `+1,220` | `+976` (`244/h`) |
| **L20** | 7,855 (`-1,309`) | 3,436 (`6,872`, `-654`) | 4,009 (`-1,472`) | `+3,273` | `+2,618` (`654/h`) |
| **L30** | 47,672 (`-7,945`) | 20,856 (`41,712`, `-3,972`) | 24,332 (`-8,938`) | `+19,863` | `+15,890` (`3,972/h`) |
| **L40** | 257,149 (`-42,858`) | 112,502 (`225,004`, `-21,429`) | 131,253 (`-48,215`) | `+107,145` | `+85,716` (`21,429/h`) |
