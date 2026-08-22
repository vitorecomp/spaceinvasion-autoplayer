# 03 — Buildings (`b1`–`b21`) & Research (`r1`–`r16`) Catalog

All integer tables (`L1`–`L100` buildings, `L1`–`L40` research) are indexed offline in [../spaceinvasion/game_data.py](../spaceinvasion/game_data.py).

---

## 1. Construction Rules `[VERIFIED-LIVE]` / `[WIKI-SPEC]`

- **REQ-BLD-001 (Building Fields)**: Every completed building level occupies **1 building field** (`fields_used / fields_max`). Starter homeworld = **230 fields** (cannot be deleted). Deleting/tearing down levels frees fields.
- **REQ-BLD-002 (Queue Limits)**: Normal planets support **5** queued construction slots (**6** with *Construction Commander* assistant). Asteroids support **1** slot (and only `astro=True` buildings plus *Research lab* via Synth node).
- **REQ-BLD-003 (Asteroid & Colony Exceptions)**:
  - **Asteroids**: Can build only `b7` (*Development center*), `b9` (*Weapons factory*), `b10` (*Fleet base*), `b17` (*Microsystem accelerator*), `b19` (*Galaxy scanner*, Asteroid-exclusive), and `b8` (only with Synth node).
  - **Raid Colonies (`[RK]`)**: Unlocked by *The Fleeter* specialization; cannot construct mines (`b1`, `b3`, `b4`), `b2`, `b5`, warehouses (`b11`–`b14`), `b8`, `b20`, or `b21`.
- **REQ-BLD-004 (Cancellation Refund)**: Canceling any building or research order refunds **70%** of resource cost (`30%` burned).

---

## 2. Complete Building Specification (`b1`–`b21`) `[WIKI-SPEC]`

| ID | Building | Astro OK | L1 Cost (`Pig / Met / Kryp / Spi`) | Scale | Max Table | Prerequisites | Primary Function |
|---|---|---|---|---|---|---|---|
| `b1` | Pig iron mine | No | `78 / 0 / 25 / 0` | `~1.51x` | L100 | None | Mines Pig iron (`27/h` L1, `-4 E`; `+2%/r1`) |
| `b2` | Blast furnace | No | `75 / 0 / 25 / 0` | `~1.51x` | L100 | None | Smelts Pig iron → Metal `2:1` (`12/h` L1, `24` Pig used; no `r1` boost) |
| `b3` | Kryptonite mine | No | `90 / 0 / 55 / 0` | `~1.51x` | L100 | None | Mines Kryptonite (`14/h` L1, `-5 E`; `+2%/r1`) |
| `b4` | Spice mine | No | `0 / 220 / 80 / 0` | `~1.51x` | L100 | None | Mines Spice (temp curve `5–8/h` L1, `-9 E`; `+2%/r1`) |
| `b5` | Fusion plant | No | `80 / 0 / 35 / 0` | `~1.51x` | L100 | None | Fuel-free power (`+11` L1, `+404` L10, `+3,273` L20) |
| `b6` | Thermosolar plant | No | `0 / 120 / 25 / 80` | `~1.51x` | L100 | `r2` L4, `b4` L6 | Spice-burning power (`+9 E`, burns `2/h` L1; mid-game backup) |
| `b7` | Development center | **Yes** | `0 / 600 / 300 / 200` | `2.0x` | L60 | None | Cuts building time by `1 / (L + 1)`; gates `b8/b9/b19/b17` |
| `b8` | Research lab | Synth only | `0 / 800 / 0 / 300` | `2.0x` | L60 | `b7` L2 | Unlocks & accelerates research on planet |
| `b9` | Weapons factory | **Yes** | `300 / 200 / 0 / 350` | `2.0x` | L60 | `r2` L1, `b7` L3 | Builds ships & defenses; cuts unit build time by `1 / L` |
| `b10` | Fleet base | **Yes** | `0 / 2,000 / 500 / 800` | `2.0x` | L60 | None | Adds concurrent fleet flight slots & supports allied stationing |
| `b11` | Pig iron warehouse | No | `2,000 / 800 / 0 / 0` | `2.0x` | L60 | None | Storage cap `100,000 * 2^L` (`L1 = 200k`, prevents 5% overflow cap) |
| `b12` | Metal warehouse | No | `2,000 / 800 / 0 / 0` | `2.0x` | L60 | None | Storage cap `100,000 * 2^L` |
| `b13` | Kryptonite warehouse | No | `2,000 / 800 / 0 / 0` | `2.0x` | L60 | None | Storage cap `100,000 * 2^L` |
| `b14` | Spice warehouse | No | `2,000 / 800 / 0 / 0` | `2.0x` | L60 | None | Storage cap `100,000 * 2^L` |
| `b15` | Particle cannon | No | `50k / 30k / 40k / 6k` | `2.0x` | L40 | `b9` L6, `r12` L5 | Stores surplus energy to fire particle attacks at enemy defenses |
| `b16` | Particle shield | No | `15k / 10k / 12k / 5k` | `2.0x` | L40 | `r12` L2, `b4` L6 | Absorbs incoming *Particle cannon* shots (priority `46%/31%/23%`) |
| `b17` | Microsystem accelerator | **Yes** | `0 / 500k / 1M / 250k` | `2.0x` | L40 | `r2` L10, `b7` L12 | Halves all building & shipyard build times per level (`0.5^L`) |
| `b18` | Teleporter | No | `100k / 140k / 150k / 50k` | `2.0x` | L30 | `r14` L6, `b17` L2 | Instant zero-flight-time fleet jump between activated teleporters |
| `b19` | Galaxy scanner | **Yes** | `10k / 40k / 20k / 8k` | `2.0x` | L35 | `b7` L8 | Asteroid-only; scans systems (`5,000 Spice/scan`); Escape flights hidden |
| `b20` | Tunnel system | No | `0 / 1M / 200k / 500k` | `1.08x` | L60 | `b9` L5 | Cuts defense dig-in time (`1h - 1m/L`); `L40+` adds particle fog |
| `b21` | Quantum Array | No | `0 / 2M / 0 / 25k` | `1.44x` | L30 | `b17` L3 | Grants +1 Synth Point/level (up to `30`, highest planet only) |

---

## 3. Infrastructure Cost & Acceleration Checkpoints (`2.0x` Scaling Buildings)

| Level | `b7` Dev Center (Cost / Time%) | `b8` Research Lab Cost | `b9` Weapons Factory (Cost / ShipTime%) | `b11`–`b14` Warehouse (Cost / Cap) |
|---|---|---|---|---|
| **L1** | `600M / 300K / 200S` (`50%`) | `800M / 300S` | `300P / 200M / 350S` (`100%`) | `2k P / 800 M` (`200,000`) |
| **L2** | `1.2kM / 600K / 400S` (`33%`) | `1.6kM / 600S` | `600P / 400M / 700S` (`50%`) | `4k P / 1.6k M` (`400,000`) |
| **L3** | `2.4kM / 1.2kK / 800S` (`25%`) | `3.2kM / 1.2kS` | `1.2kP / 800M / 1.4kS` (`33%`) | `8k P / 3.2k M` (`800,000`) |
| **L5** | `9.6kM / 4.8kK / 3.2kS` (`17%`) | `12.8kM / 4.8kS` | `4.8kP / 3.2kM / 5.6kS` (`20%`) | `32k P / 12.8k M` (`3.2M`) |
| **L10** | `307.2kM / 153.6kK / 102.4kS` (`9.1%`) | `409.6kM / 153.6kS` | `153.6kP / 102.4kM / 179.2kS` (`10%`) | `1.024M P / 409.6k M` (`102.4M`) |
| **L15** | `9.83M M / 4.92M K / 3.28M S` (`6.3%`) | `13.11M M / 4.92M S` | `4.92M P / 3.28M M / 5.73M S` (`6.7%`) | `32.77M P / 13.11M M` (`3.2768B`) |
| **L20** | `314.57M M / 157.29M K / 104.86M S` (`4.8%`) | `419.43M M / 157.29M S` | `157.29M P / 104.86M M / 183.5M S` (`5.0%`) | `1.05B P / 419.43M M` (`104.86B`) |

---

## 4. Complete Research Catalog (`r1`–`r15` + Special) `[WIKI-SPEC]`

- **REQ-RES-001 (Account-Wide Scope)**: All researches apply globally across every planet, asteroid, and ship immediately upon completion.
- **REQ-RES-002 (Cost Doubling & Time Scaling)**: `r1`–`r15` scale costs and research times by **exact `2.0x` per level** (`base * 2^(L - 1)`), accelerated by the local planet's *Research lab* (`b8`) level (or summed labs in *Sirius* research-network universe).

| Key | Research | L1 Cost (`Pig / Met / Kryp / Spi`) | L1 Time (`1x`) | Max Table | Prerequisites | Strategic Effect & Priority |
|---|---|---|---|---|---|---|
| `r1` | Mining technology | `1,000 / 0 / 500 / 0` | `2,083s` | L40 | `r2` L4, `b8` L2 | `+2%/lvl` Pig, Kryp, Spice across all planets (NOT `b2`) |
| `r2` | Energy technology | `0 / 400 / 800 / 400` | `1,833s` | L40 | `b8` L1 | `+1%/lvl` global energy; gates `b9`, `b6`, `r1`, shields |
| `r3` | Engine research | `0 / 800 / 0 / 250` | `1,000s` | L35 | `b8` L1 | Gates Rocket (`L2`), Diffusion (`L6`), Warp (`L8`) engines |
| `r4` | Rocket engine | `0 / 400 / 350 / 100` | `1,083s` | L40 | `r3` L2 | `+10%/lvl` speed on early ships/probes/colony ships |
| `r5` | Diffusion engine | `0 / 300 / 300 / 200` | `875s` | L40 | `r3` L6 | `+10%/lvl` speed on *Phoenix*, *Starcruiser*, *Transmitter*, *Recycler* |
| `r6` | Warp engine | `0 / 5k / 2k / 1k` | `9,583s` | L35 | `r3` L8, `b8` L8 | `+10%/lvl` speed on *Battleship*, *Stealth bomber*, *Destroyer*, *Starbase* |
| `r7` | Spy technology | `0 / 500 / 2k / 300` | `3,958s` | L35 | `b8` L3 | Improves spy report detail, counter-espionage, Stealth concealment |
| `r8` | Weapons technology | `0 / 2k / 100 / 0` | `2,667s` | L40 | `b8` L3 | **`+10%/lvl` ship/defense attack power** |
| `r9` | Shield technology | `0 / 250 / 2k / 0` | `3,646s` | L40 | `b8` L4, `r2` L4 | **`+10%/lvl` unit shield capacity** |
| `r10` | Defense systems | `0 / 2.5k / 0 / 0` | `3,125s` | L40 | `b8` L5 | **`+10%/lvl` unit hull (armor)**; gates *Phoenix* (`L5`) |
| `r11` | Targeting systems | `0 / 1k / 2k / 500` | `4,583s` | L35 | `b8` L5, `r2` L6 | Unlocks heavy planetary turret tiers (`Ion`, `Positron`, `Laser`, `Solar`) |
| `r12` | Particle analyzer | `0 / 5k / 8k / 10k` | `19,583s` | L30 | `b8` L6, `r9` L5 | Gates *Particle shield* (`L2`), *Interval cannon* (`L4`), *Particle cannon* (`L5`) |
| `r13` | Graviton technology | `0 / 300 / 2k / 300` | `3,708s` | L35 | `b8` L2, `r2` L2 | Gates graviton turrets & *Black hole research* (`L10`) |
| `r14` | Teleportation | `0 / 100k / 80k / 20k` | `258,333s` | L30 | `b8` L10 | Gates *Teleporter* (`L6`); lowers teleport cooldown / energy use |
| `r15` | Black hole research | `0 / 500k / 1M / 250k` | `2,291,667s` | L25 | `r6` L10, `r13` L10 | Gates *Imperial starbase* (`L2`) & extends scanner jam (`4h + 1h/lvl`) |
| — | Urplasma-extraction | Special | Special | — | `b8` L10 | Non-standard universe extraction unlock (no `r1`–`r15` ID on RETRO) |

*Note*: The Wiki's "Super Computer" page describes the *Super Computer* capacity unit built inside *Research labs* (`b8 >= 15`) to accelerate running research for Spice, rather than a technology level in `r1`–`r15`.

---

## 5. Core Dependency DAG

```text
Development center (b7)
 ├── L2 ──▶ Research lab (b8) ──▶ Energy tech (r2), Engine research (r3)
 │            ├── r2 L1 + b7 L3 ──▶ Weapons factory (b9)
 │            ├── r2 L4 + b8 L2 ──▶ Mining tech (r1)
 │            └── r2 L4 + b4 L6 ──▶ Thermosolar plant (b6)
 ├── L8 ──▶ Galaxy scanner (b19, Asteroid)
 └── L12 + r2 L10 ──▶ Microsystem accelerator (b17) ──▶ L3 ──▶ Quantum Array (b21)
```
