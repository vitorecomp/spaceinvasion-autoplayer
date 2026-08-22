# SpaceInvasion Classic — Software Requirements & Game Mechanics Specifications

This directory specifies the complete **SpaceInvasion Classic** domain mechanics, growth equations, reference tables, web UI contracts, and automation requirements for `spaceinvasion-autoplayer`.

For the 3-tier runtime architecture (`Chrome @ :9222` → `game_api.py @ :8787` → `commander.py`) and milestone roadmap, see [../SPEC.md](../SPEC.md).

## Specification Documents

| File | Scope | Verification Basis |
|---|---|---|
| [01-ui-pages-and-clicks.md](01-ui-pages-and-clicks.md) | BitMeUp auth, 3-iframe shell, every `F_Navi` screen, selectors, click/POST flows, rate/anti-bot guards | Tagged `[VERIFIED-LIVE]` vs `[WIKI-SPEC]` |
| [02-economy-and-formulas.md](02-economy-and-formulas.md) | Resources, scaling formulas, temperature curves, Merchant `6:2.5:3:1`, Speed-Up tiers, energy grid, warehouses | Verified against [../spaceinvasion/game_data.py](../spaceinvasion/game_data.py) & live RETRO |
| [03-buildings-and-research.md](03-buildings-and-research.md) | All 21 buildings (`b1`–`b21`) & 16 researches (`r1`–`r16`), prerequisites DAG, queue mechanics, checkpoints | Verified against Wiki tables & [../spaceinvasion/game_data.py](../spaceinvasion/game_data.py) |
| [04-ships-defense-and-combat.md](04-ships-defense-and-combat.md) | All 15 ships, 6 outpost variants, 11 defenses, `1:1` zero-rapidfire combat model, Stealth/Dig-in/Particle shields | Verified against Wiki unit sheets & combat docs |
| [05-fleet-missions-and-progression.md](05-fleet-missions-and-progression.md) | Fleet missions, Escape Flight, AZ/Bash rules, Colonization (`>=230` fields vs cold Spice), **Full-Auto Aggressive Fleeter** doctrine, Synth & Aliens | Verified against Wiki guides & `board.bitmeup.com` |

## Requirement ID Conventions & Source Policy

- Every behavioral or domain rule carries a stable identifier:
  - `REQ-UI-*`: Browser shell, iframe navigation, DOM selectors, clicks, form POSTs, and guards.
  - `REQ-ECO-*`: Resource extraction, Blast furnace conversion, energy balance, storage caps, Merchant/Speed-Up.
  - `REQ-BLD-*` / `REQ-RES-*`: Building/research costs, growth multipliers, prerequisites, queues, and cancel refunds.
  - `REQ-CMB-*`: Unit statistics, zero-rapidfire resolution, Stealth strike, Dig-in, Particle weapons, and safety margins.
  - `REQ-FLT-*` / `REQ-PRO-*`: Escape Flights, Galaxy map rules, Bash/harassment caps, colonization, Fleeter doctrine, and endgame systems.
- **Live vs. Wiki Tags**:
  - `[VERIFIED-LIVE]`: Observed directly in the attached RETRO Chrome DOM/network session.
  - `[WIKI-SPEC]`: Derived from the official SpaceInvasion Classic Wiki (`https://spaceinvasion.bitmeup.com/wiki/en/`) and awaiting live DOM capture once unlocked on the test account.
- **Full Level Tables**: Checkpoints (`L1`–`L15`, `L20`, `L30`, `L40`) appear inside these Markdown specs; exact integer tables for all levels (`1–100` buildings, `1–40` research) are stored offline in [../spaceinvasion/game_data.py](../spaceinvasion/game_data.py) via [../scripts/sync_wiki_data.py](../scripts/sync_wiki_data.py).
