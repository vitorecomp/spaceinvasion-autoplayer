# 04 — Ships (`s1`–`s16`), Defenses (`d1`–`d11`) & Combat Engine

All ship and defense stats match official Wiki fact sheets indexed in [../spaceinvasion/game_data.py](../spaceinvasion/game_data.py).

---

## 1. Standard Ships Catalog `[WIKI-SPEC]`

Engine speed bonus: **`+10%` per level** of the ship's governing engine (`Rocket`, `Diffusion`, `Warp`). Every multi-ship fleet travels strictly at the speed of its **slowest ship**.

| Key | Ship | Cost (`P / M / K / S`) | Hull | Shield | Attack | Base Speed | Cargo | Fuel | Engine | Prerequisites |
|---|---|---|---|---|---|---|---|---|---|---|
| `s1` | Small transport | `0 / 3k / 3k / 0` | 6,000 | 10 | 2 | 3,000 | 6,000 | 10 | Rocket | `b9` L2, `r4` L4 |
| `s2` | Large transport | `0 / 8k / 6k / 0` | 14,000 | 25 | 5 | 5,000 | 30,000 | 15 | Rocket | `b9` L6, `r4` L6 |
| `s3` | **Transmitter** | `0 / 25k / 25k / 0` | 50,000 | 70 | 130 | 7,000 | **250,000** | 25 | Diffusion | `b9` L8, `r5` L8 |
| `s4` | **Small fighter** | `0 / 4k / 2k / 100` | 6,000 | 10 | 50 | **9,000** | 60 | 15 | Rocket | `b9` L1, `r4` L1 |
| `s5` | Warship | `0 / 8k / 4k / 300` | 12,000 | 30 | 90 | 6,500 | 100 | 28 | Rocket | `b9` L4, `r4` L4, `r8` L2 |
| `s6` | Starcruiser | `0 / 25k / 10k / 1.5k` | 35,000 | 100 | 250 | 4,800 | 200 | 40 | Diffusion | `b9` L5, `r5` L5, `r9` L4 |
| `s7` | **Phoenix** | `0 / 30k / 20k / 5k` | 50,000 | 180 | 320 | **8,000** | 500 | 55 | Diffusion | `b9` L7, `r5` L8, `r10` L5 |
| `s8` | Colony ship | `10k / 20k / 15k / 8k` | 45,000 | 80 | 10 | 1,500 | 5,000 | 32 | Rocket | `b9` L4, `r4` L8 |
| `s9` | **Solar cells** | `0 / 2k / 200 / 400` | 2,200 | 5 | 0 | 0 | 0 | 0 | Stationary | `b9` L1 (**0 fields used**) |
| `s10` | Spy probe | `0 / 0 / 3k / 200` | 3,000 | 6 | 0 | 15,000,000 | 6 | 3 | Rocket | `b9` L1, `r4` L3, `r7` L2 |
| `s11` | **Recycler** | `2k / 10k / 5k / 0` | 17,000 | 30 | 8 | 2,000 | 25,000 | 20 | Diffusion | `b9` L4, `r5` L4 |
| `s13` | **Battleship** | `0 / 35k / 25k / 0` | 60,000 | 300 | 300 | 6,800 | 1,000 | 90 | Warp | `b9` L8, `r6` L4 |
| `s14` | **Stealth bomber** | `0 / 40k / 60k / 10k` | 100,000 | 100 | 300 | 5,200 | 1,400 | 110 | Warp | `b9` L10, `r7` L8, `r6` L6 |
| `s15` | **Destroyer** | `0 / 60k / 50k / 2k` | 110,000 | 400 | 700 | 4,000 | 3,000 | 130 | Warp | `b9` L10, `r6` L8 |
| `s16` | Imperial starbase | `0 / 3M / 2M / 500k` | 5,000,000 | 15,000 | 35,000 | 300 | 3,500,000 | 250 | Warp | `b9` L13, `r15` L2 |

- **Advanced Outpost / Event Variants** (`ships/ship-versions`): `s104` (*Small hunter*), `s106` (*Titan Carrier*), `s206` (*Hangar Ship*), `s111` (*Collector Swarm*), `s113` (*Void Shield*), `s115` (*The Doombringer*).
- **Imperial Starbase Self-Destruct**: Sacrificing an *Imperial starbase* in a target system blinds all enemy Asteroid *Galaxy scanners* in that system (showing colonization flights only) for **`4h + 1h * r15 (Black hole research)`**.

### Orbital Solar Cell Temperature Output (`s9`) `[WIKI-SPEC]`

| Temperature (°C) | `-160` | `-120` | `-80` | `-40` | `0` | `+40` | `+80` | `+120` | `+160` | `+200` |
|---|---|---|---|---|---|---|---|---|---|---|
| **Energy / Cell** | `+10` | `+10` | `+10` | `+20` | `+30` | **`+40`** | **`+50`** | **`+60`** | **`+70`** | **`+80`** |

---

## 2. Planetary Defense Catalog (`d1`–`d11`) `[WIKI-SPEC]`

| Key | Defense | Cost (`M / K / S`) | Hull | Shield | Attack | Prerequisites |
|---|---|---|---|---|---|---|
| `d1` | Mortar | `2k / 0 / 0` | 2,000 | 15 | 35 | `b9` L1 |
| `d2` | Light graviton cannon | `2k / 1k / 0` | 3,000 | 25 | 50 | `b9` L3, `r2` L3, `r13` L2 |
| `d3` | Heavy graviton cannon | `5k / 2k / 0` | 7,000 | 60 | 115 | `b9` L5, `r11` L5, `r13` L6 |
| `d4` | Ion cannon | `2k / 8k / 0` | 10,000 | 125 | 125 | `b9` L5, `r11` L4 |
| `d5` | Turboflak | `10k / 2k / 0` | 12,000 | 80 | 220 | `b9` L6, `r2` L6 |
| `d6` | Positron lighter | `10k / 30k / 100` | 40,000 | 200 | 700 | `b9` L6, `r11` L8, `r10` L6 |
| `d7` | Interval cannon | `15k / 15k / 1.5k` | 30,000 | 100 | 900 | `b9` L7, `r12` L4, `r11` L10 |
| `d8` | Laser cannon battery | `20k / 20k / 500` | 40,000 | 400 | 1,000 | `b9` L8, `r11` L10, `r2` L8 |
| `d9` | Solar cannon | `40k / 80k / 8k` | 120,000 | 1,000 | 2,000 | `b9` L10, `r11` L10, `r2` L10 |
| `d10` | Small shield dome | `8k / 5k / 3k` | 13,000 | 3,000 | 0 | `b9` L4, `r2` L4, `r9` L2 |
| `d11` | Large shield dome | `25k / 15k / 8k` | 40,000 | 30,000 | 0 | `b9` L6, `r2` L8, `r9` L4 |

---

## 3. Combat Engine & Fleet Doctrine `[WIKI-SPEC]`

- **REQ-CMB-001 (Zero Rapidfire — `1:1` Targeting)**: Unlike OGame, **no ship gets bonus rapidfire shots**. Every firing unit targets cleanly without rapidfire cascades. **Meta consequence**: Mass cheap **Small fighters (`s4`)** soak heavy single shots from *Destroyers* and *Solar cannons* efficiently as cannon fodder.
- **REQ-CMB-002 (Round Limit & Stat Scaling)**: Battles last **at most 6 rounds** (`7` with a Synth battle node). Shields absorb first; remaining damage hits armor. Global tech scaling is **`+10%` per level** for Attack (`r8`), Shields (`r9`), and Hull/Defense (`r10`).
- **REQ-CMB-003 (Stealth Bomber Pre-Round First Strike)**: Before round 1 begins, attacking **Stealth bombers (`s14`)** fire a surprise volley exclusively targeting non-dug-in planetary defenses, weakening bunkers before normal rounds start. Stealth bombers are detected late while flying depending on enemy `r7` *Spy technology*.
- **REQ-CMB-004 (Defense Dig-In)**: On supported universes (GeNeSIS, Sirius, Infinity), non-dome turrets (`d1`–`d9`) can dig in (`1h - 1m / b20` *Tunnel system* level) and **do not participate in battle or suffer Stealth bomber strikes** (blocks new turret builds while dug in).
- **REQ-CMB-005 (Particle Attacks & Shields)**: *Particle cannons* (`b15`) fire remotely at planet defenses; enemy *Particle shields* (`b16`) intercept incoming beams in a `46% / 31% / 23%` priority split.
- **REQ-CMB-006 (Random Battle Luck & Safety Margin)**: Both sides receive a random luck roll per engagement. Automated attacks **MUST** enforce an estimated power ratio `>= 1.4x` (`combat_safety_margin`) to guarantee win consistency. Draws force attacker retreat with **0 loot**.
- **REQ-CMB-007 (Loot, Debris & Repairs)**: Winners plunder up to **50%** of planet resources (higher with *Fleeter*/items/Synth), capped by surviving cargo hold. Destroyed units drop a harvestable debris field (and can spawn an Asteroid if large enough). Most defenses and orbital *Solar cells* regenerate automatically after battle.

### Recommended Fleeter Wing Combinations
1. **Diffusion Fast-Raid Wing**: **Phoenix (`s7`, speed `8,000`) + Starcruiser (`s6`) + Transmitter (`s3`, speed `7,000`, `250k` cargo + `130` attack)** — fast strikes where *Transmitters* haul massive loot and contribute combat damage.
2. **Warp Bunker-Buster Wing**: **Stealth bomber (`s14`, defense first-strike) + Battleship (`s13`, `0` Spice cost) + Destroyer (`s15`, `700` attack anchor)** paired with **Small fighter (`s4`)** fodder for heavy defended targets.
