# 05 — Fleet Missions, Escape Flight, Galaxy Rules & Full-Auto Fleeter Progression

---

## 1. Fleet Missions & Escape Flight Safety `[WIKI-SPEC]`

- **REQ-FLT-001 (Fleet Missions Matrix)**:
  - **Transport**: Delivers cargo to own or allied planets and returns empty (blocked toward inactive targets).
  - **Station**: Permanently relocates fleet + cargo to own planet/asteroid.
  - **Escape flight (Fleet Save)**: Launches stationed fleet + cargo to a target coordinate and automatically reverses home so nothing is catchable offline.
  - **Spying**: *Spy probes* (`s10`) recon foreign planets (or sample uninhabited slot ecology/fields).
  - **Colonize**: One *Colony ship* (`s8`) settles an empty slot (ship consumed; escort & cargo settle planet). Returned if slot is taken or empire planet limit is reached.
  - **Recycle**: *Recyclers* (`s11`) harvest floating debris fields (first-come, first-served; locked temporarily after planet relocation).
  - **Attack / AKS**: Individual battle mission or joint Asteroid *Alliance Attack (AKS)*.
  - **Teleport**: Instantaneous jump between two planets with active *Teleporters* (`b18`).
- **REQ-FLT-002 (Escape Flight Invisibility & Packing Protocol)**:
  - **No fleet in transit can be attacked.**
  - **Escape flights are 100% invisible to Asteroid *Galaxy scanners*.**
  - Intra-galaxy flights support picking a **shorter or longer route** to stretch return timing until next login.
  - Cargo packing strictly prioritizes highest resource value density: **Spice → Metal → Kryptonite → Pig iron**, holding back return **fuel Spice** inside cargo capacity (`plan_escape_flight`).
  - Recalling a running Escape Flight takes elapsed flight time (or **half elapsed time** with *Escape flight assistant+*). Never stay offline with fleets idle on-planet (*"recall-dodge"* fires right before impact and recalls after).

---

## 2. Galaxy Map, Attack Zones, Bash & Harassment Rules `[WIKI-SPEC]`

- **REQ-FLT-010 (Universe Grid & Galaxy Colors)**: Systems contain `1–16` slots. Galaxy colors define legal interactions:
  - **Light red**: Within active Attack Zone (`AZ ±2`, or `25%–400%` points on Sirius; from zone 18 / `250M` pts everyone is reachable).
  - **Bright red**: Top-50 overall accounts (attackable by anyone; grants `14-day` retaliation window) or recent attackers (`14 days`).
  - **Yellow**: Inactive **> 7 days** OR **Spacia** AI worlds — **attackable by anyone regardless of AZ**.
  - **Dark red**: Too strong (`> +2 AZ`). Protected.
  - **Blue**: Too weak (`< -2 AZ`) or in *Vacation Mode*.
  - **White**: *Spectral Mode* (immune to attacks).
  - **Green / Turquoise**: Alliance / allied partners.
  - **Purple / Pink**: Game Operators (`GO`).
- **REQ-FLT-011 (Newbie & IP Protection)**: Players below **`10,000` points** cannot be attacked and cannot launch PvP attacks. IP match entries, active sitting sessions, and recent planet relocations also block attacks.
- **REQ-FLT-012 (Bash Rule — `3 / 24h`)**: Standard rules cap attacks at **3 attacks per 24 hours** per active player planet/asteroid (**5 / 24h** during official alliance war/invasion). **Yellow inactive players and Spacia planets are EXEMPT from the Bash limit** (`BashTracker`).
- **REQ-FLT-013 (Harassment / Virulent Attacks)**: Attacking vastly weaker players triggers a *virulent/harassment* warning costing 1 temporary point (`synth-points`) until decay.

---

## 3. Colonization Strategy (`>= 230` Fields vs. Cold Spice) `[WIKI-SPEC]`

- **REQ-PRO-001 (Orbital Ecology)**: Homeworld has **230 fields** (undeletable). Mid-to-outer slots (`positions 7–12`) statistically roll larger planet field counts (`230–300+`), whereas hot inner (`1–3`) favor **Solar cells** and far outer edges (`13–16`) produce peak **Spice**.
- **REQ-PRO-002 (Probe-Before-Colony Protocol)**: Send cheap *Spy probes* to candidate empty coordinates first to read uninhabited ecology field counts before dispatching *Colony ships* (`pick_colony_candidates`).
- **REQ-PRO-003 (Large Economy Worlds vs. Cold Spice Worlds)**:
  - **Primary Economy Colonies**: Target **`>= 230` building fields** (veterans often send staggered Colony ships and abandon small rolls <230 in Settings so the following Colony ship re-rolls). Keep core clusters close for low-fuel saves and fast transport.
  - **Cold *Spice* Colonies**: Edge slots (`13–16`) remain worth keeping even under 230 fields specifically to run *Spice mine* + *Fusion/Thermosolar* + minimal warehouses (feeding constant Fleeter propellant), skipping redundant *Research labs* and *Blast furnaces*.
  - **Fleeter `[RK]` Raid Colonies**: Special non-producing staging bases unlocked by *The Fleeter* specialization, excluded from normal colony caps (`typically 11 base`), placed directly inside target hunting systems.

---

## 4. Target Autoplayer Doctrine: Full-Auto Aggressive Fleeter `[USER-REQ]`

Configured via `playstyle: fleeter` in [../config.example.yaml](../config.example.yaml):

1. **REQ-PRO-010 (Tutorial 1–11 Rapid Climb)**:
   - Execute relative step requirements (`building` + `research`), pushing *Development center* early and deferring non-urgent *Fusion plant* upgrades (`bucket = 4`) behind mines whenever projected net energy covers upcoming mine deltas.
   - Auto-claim completed rewards (`tutorialComplete=1`) to reach `Research lab` + `Weapons factory` fast.
2. **REQ-PRO-011 (Fleeter Tech & Shipyard Ramp)**:
   - Prioritize `r2` (*Energy L1/L4*) + `r3` (*Engine research*) → `r4` (*Rocket*) / `r5` (*Diffusion*) / `r6` (*Warp*), `r7` (*Spy tech*), and combat trio (`r8` *Weapons*, `r9` *Shield*, `r10` *Defense*), balancing cross-planet `r1` (*Mining tech*) when Spice fuel demand requires higher `b4` sustain.
   - Maintain baseline utility (`s1` *Small transport*, `s10` *Spy probe*, `s8` *Colony ship*, `s11` *Recycler*), mass `s4` (*Small fighter*) fodder, and scale directly into **Phoenix (`s7`) + Transmitter (`s3`)** and **Stealth bomber (`s14`) + Battleship (`s13`) + Destroyer (`s15`)** wings as unlocked (`shipyard_targets`).
3. **REQ-PRO-012 (Safe Target Default — Yellow / Spacia / Debris First)**:
   - Automated scanning, espionage, and raid scoring (`score_raid_target`) target **Yellow inactives, overlooked distant *Spacia* worlds** (which level mines weekly when left unfarmed, whereas heavily raided neighbor *Spacia* planets downgrade), and unguarded **Debris fields** by default.
   - Active PvP against **Light Red / Top-50** players remains **blocked by default** unless `allow_pvp_in_az: true` is explicitly configured by the operator.
4. **REQ-PRO-013 (Mandatory Fleet Safety)**:
   - Before ending active play or whenever `fleet_alerts` indicates hostile incoming impact, `P0` auto-packs stationed ships (`Spice → Metal → Kryptonite → Pig iron`, reserving return fuel Spice) via **Escape flight** (`allow_fleetsave`).

---

## 5. Progression & Endgame Systems `[WIKI-SPEC]`

- **Specializations (`internalSpecs`)**: Choose **The Fleeter** (`[RK]` raid colonies, reduced fuel consumption, engine/recycler bonuses, extra Spacia attacks, higher resource plunder), **The Miner** (mine output, +cargo, stronger domes, cheaper defense), **The Researcher** (daily research speed-up, resource bunker floor, lower fuel), or **Alien** tiers.
- **Synth Network (`internalSynth`)**: 346-node web powered by up to **80 Synth points** (*Quantum Array* max 30 + VIP/Assistants/Account health). Convert Spice into Synth energy & Cores via *Synth Former* + *AI-Core* (1d/3d/7d programs) to unlock **battle**, **escape-flight**, **recycle**, **spy**, and **extra-round (7-round)** nodes.
- **Weekly Aliens & Black Holes (`internalAlien`)**: Unlocked at **10,000 tactical fleet strength** (Friday–Friday cycle). Commit virtual wave power, claim daily reinforcement, and hit White/Green/Purple/Red/Orange black holes using free daily invisible attacks up to personal Spice & War Emblem caps.
- **Spacia Raid Event & IDT**: During periodic *Spacia Raid* events, mine yields surge and looted Spice ranks top-20 players for War Emblems (`#1: 5,000`, `2–5: 2,500`, `6–10: 1,000`, `11–20: 500`; loot rate halves past 10B Spice, quarters past 15B). Claim free *Interdimensional Transmitter* (`internalTransmitter`) spins on homeworld for bonus fields, assistants, fleets, or Planet Move jackpots.
