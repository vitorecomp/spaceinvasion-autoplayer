"""Shared deterministic multi-galaxy, multi-colony fixtures for Wave B parity tests."""

from __future__ import annotations

from collections.abc import Sequence

from spaceinvasion.parsing.models import GalaxySlot as CombatGalaxySlot
from spaceinvasion.parsing.models import (
    InboxMessage,
    SpyReport,
)
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    Resources,
    TutorialStatus,
    TutorialTask,
)
from spaceinvasion.db.repos import GalaxySlotInput, GameMessageInput, PlanetSnapshotInput, SpyReportInput

T0: float = 1700010000.0


def build_game_state() -> GameState:
    """Build a deterministic 2-colony `GameState` with tutorial progress."""
    cap = Resources(pig_iron=200000, metal=200000, kryptonite=100000, spice=100000)
    p1 = PlanetState(
        planet_id=101,
        name="Homeworld",
        coords="02:380:12",
        planet_type="normal",
        temperature=24,
        fields_used=92,
        fields_max=220,
        resources=Resources(pig_iron=50000, metal=30000, kryptonite=18000, spice=7500),
        storage=cap,
        energy=EnergyBalance(produced=4500, used=3800),
        buildings={"b1": 18, "b2": 14, "b3": 12, "b4": 10},
        ships={"s1": 12, "s6": 4},
        defense={"d1": 20},
        economy_rates={"b1": 100, "b2": 100, "b3": 90},
    )
    p2 = PlanetState(
        planet_id=102,
        name="SpiceColony",
        coords="03:120:07",
        planet_type="normal",
        temperature=-12,
        fields_used=45,
        fields_max=332,
        resources=Resources(pig_iron=22000, metal=14000, kryptonite=9000, spice=12000),
        storage=cap,
        energy=EnergyBalance(produced=2800, used=2100),
        buildings={"b1": 12, "b4": 14},
        ships={"s5": 5},
        defense={},
        economy_rates={"b1": 100, "b4": 100},
    )
    tut = TutorialStatus(
        step=4,
        total_steps=11,
        tasks=(TutorialTask(description="Upgrade mine", category="build", current=1, target=1),),
        reward_text="2500 Pig iron",
        can_complete=True,
    )
    return GameState(
        timestamp=T0,
        universe_id=1,
        user_id=42,
        nickname="Commander",
        active_planet_id=101,
        planets=(p1, p2),
        researches={"r1": 8, "r3": 6},
        account_points=125000,
        account_rank=17,
        tutorial=tut,
    )


def to_planet_inputs(state: GameState) -> list[PlanetSnapshotInput]:
    """Convert `GameState.planets` into typed `PlanetSnapshotInput` records."""
    return [
        PlanetSnapshotInput(
            planet_id=p.planet_id,
            coords=p.coords,
            name=p.name,
            planet_type=p.planet_type,
            temperature=p.temperature,
            fields_used=p.fields_used,
            fields_max=p.fields_max,
            pig_iron=p.resources.pig_iron,
            metal=p.resources.metal,
            kryptonite=p.resources.kryptonite,
            spice=p.resources.spice,
            energy_produced=p.energy.produced,
            energy_used=p.energy.used,
            buildings=p.buildings,
            ships=p.ships,
            defense=p.defense,
            economy_rates=p.economy_rates,
        )
        for p in state.planets
    ]


def galaxy_slot_specs() -> tuple[tuple[GalaxySlotInput, ...], tuple[GalaxySlotInput, ...]]:
    """Return deterministic slots across galaxies G2:S380 and G3:S120."""
    g2 = (
        GalaxySlotInput("02:380:04", 4, "InactiveOutpost", "OldRaider", "yellow"),
        GalaxySlotInput(
            "02:380:05",
            5,
            "RubbleField",
            "ActiveFoe",
            "white",
            False,
            True,
            True,
            None,
            None,
            None,
            None,
            8000,
            4000,
            0,
            0,
        ),
        GalaxySlotInput("02:380:06", 6, "Spacia", "Spacia", "purple", True),
        GalaxySlotInput("02:380:08", 8, "", "", "free", False, False, False, None, 240, 330, 35),
        GalaxySlotInput("02:380:09", 9, "", "", "free"),
        GalaxySlotInput("02:380:12", 12, "Homeworld", "Commander", "white"),
    )
    g3 = (
        GalaxySlotInput(
            "03:120:03",
            3,
            "RimFarm",
            "InactiveTwo",
            "yellow",
            False,
            True,
            False,
            None,
            None,
            None,
            None,
            3000,
            1500,
            0,
            0,
        ),
        GalaxySlotInput("03:120:07", 7, "SpiceColony", "Commander", "white"),
        GalaxySlotInput("03:120:09", 9, "ActiveFort", "DefenderB", "white"),
        GalaxySlotInput("03:120:10", 10, "", "", "free", False, False, False, None, 260, 345, 18),
    )
    return g2, g3


def to_combat_slots(specs: Sequence[GalaxySlotInput]) -> list[CombatGalaxySlot]:
    """Convert `GalaxySlotInput` specs into legacy `CombatGalaxySlot` objects."""
    return [
        CombatGalaxySlot(
            position=s.position,
            coords=s.coords,
            name=s.planet_name,
            owner=s.owner,
            status_color=s.status_color,
            is_spacia=s.is_spacia,
            has_debris=s.has_debris,
            has_asteroid=s.has_asteroid,
            colonize_min_fields=s.colonize_min_fields,
            colonize_max_fields=s.colonize_max_fields,
            temperature=s.temperature,
            debris_pig_iron=s.debris_pig_iron,
            debris_metal=s.debris_metal,
            debris_kryptonite=s.debris_kryptonite,
            debris_spice=s.debris_spice,
        )
        for s in specs
    ]


def inbox_message_batches() -> tuple[tuple[GameMessageInput, ...], tuple[GameMessageInput, ...]]:
    """Return two sequential inbox batches testing initial insert + SQL `ON CONFLICT` enrichment."""
    batch_1 = (
        GameMessageInput(
            msg_uid="msg-gen-1",
            server_msg_id="srv-100",
            folder_type="inbox",
            category="player",
            msg_date_text="2026-11-20 10:00",
            sender="AllyOne",
            subject="Trade offer",
            coords="02:380:04",
            body_text="Short trade ping",
            raw_html="<p>Short trade ping</p>",
            parsed_data={"note": "short"},
        ),
        GameMessageInput(
            msg_uid="msg-bat-1",
            server_msg_id="srv-101",
            folder_type="combat",
            category="battle_report",
            msg_date_text="2026-11-20 10:05",
            sender="FleetCommand",
            subject="Battle report 02:380:04",
            coords="02:380:04",
            body_text="Battle won at 02:380:04",
            raw_html="<p>Battle won</p>",
            parsed_data={
                "battle_id": "bat-42",
                "attacker": "Commander",
                "defender": "OldRaider",
                "outcome": "win",
                "rounds": 2,
                "loot": {"pig_iron": 12000, "metal": 6000, "kryptonite": 3000, "spice": 900},
                "attacker_lost_spice": 120,
                "defender_lost_spice": 4500,
                "debris": {"pig_iron": 2400, "metal": 1200},
            },
        ),
        GameMessageInput(
            msg_uid="msg-rec-1",
            server_msg_id="srv-102",
            folder_type="fleet",
            category="recycle_report",
            msg_date_text="2026-11-20 10:08",
            sender="FleetCommand",
            subject="Recycle report 02:380:05",
            coords="02:380:05",
            body_text="Recyclers collected rubble at 02:380:05",
            raw_html="<p>Recycled</p>",
            parsed_data={
                "recycler_count": 4,
                "cargo_capacity": 80000,
                "debris_available": {"pig_iron": 8000, "metal": 4000},
                "harvested": {"pig_iron": 8000, "metal": 4000, "kryptonite": 0, "spice": 0},
            },
        ),
        GameMessageInput(msg_uid="id:msg-ignored", subject="Ignore", body_text="Skip synthetic id"),
    )
    batch_2 = (
        GameMessageInput(
            msg_uid="msg-gen-1",
            server_msg_id="srv-100",
            folder_type="inbox",
            category="player",
            msg_date_text="2026-11-20 10:00",
            sender="AllyOne",
            subject="Trade offer",
            coords="02:380:04",
            body_text="Expanded trade ping with full resource offer details",
            raw_html="<p>Expanded trade ping with full resource offer details</p>",
            parsed_data={"note": "expanded"},
        ),
    )
    return batch_1, batch_2


def to_legacy_messages(specs: Sequence[GameMessageInput]) -> list[InboxMessage]:
    """Convert `GameMessageInput` specs into legacy `InboxMessage` objects."""
    return [
        InboxMessage(
            msg_uid=m.msg_uid,
            server_msg_id=m.server_msg_id,
            folder_type=m.folder_type,
            category=m.category,
            msg_date_text=m.msg_date_text,
            sender=m.sender,
            subject=m.subject,
            coords=m.coords,
            body_text=m.body_text,
            raw_html=m.raw_html,
            parsed_data=dict(m.parsed_data),
        )
        for m in specs
    ]


def spy_inputs() -> tuple[SpyReportInput, SpyReportInput]:
    """Return one inactive farm spy report and one uninhabited colony probe report."""
    rep_inactive = SpyReportInput(
        target_coords="2:380:4",
        owner="OldRaider",
        planet_name="InactiveOutpost",
        is_spacia=False,
        pig_iron=45000,
        metal=25000,
        kryptonite=12000,
        spice=4000,
        buildings={"b1": 15, "b2": 12},
        fleet={"s1": 2},
        defense={"d1": 5},
        researches={"r1": 7},
        dug_in=False,
        server_msg_id="spy-201",
        msg_date_text="2026-11-20 10:10",
        raw_text="Espionage report for 02:380:04",
    )
    rep_colony = SpyReportInput(
        target_coords="2:380:8",
        owner="",
        planet_name="",
        is_spacia=False,
        uninhabited_fields=335,
        server_msg_id="spy-202",
        msg_date_text="2026-11-20 10:12",
        raw_text="Uninhabited planet at 02:380:08 with 335 fields",
    )
    return rep_inactive, rep_colony


def to_legacy_spy(inp: SpyReportInput) -> SpyReport:
    """Convert `SpyReportInput` into a legacy `SpyReport` object."""
    return SpyReport(
        target_coords=inp.target_coords,
        owner=inp.owner,
        planet_name=inp.planet_name,
        is_spacia=inp.is_spacia,
        resources=Resources(inp.pig_iron, inp.metal, inp.kryptonite, inp.spice),
        buildings={k: int(v) for k, v in inp.buildings.items() if isinstance(v, int)},
        fleet={k: int(v) for k, v in inp.fleet.items() if isinstance(v, int)},
        defense={k: int(v) for k, v in inp.defense.items() if isinstance(v, int)},
        researches={k: int(v) for k, v in inp.researches.items() if isinstance(v, int)},
        dug_in=inp.dug_in,
        uninhabited_fields=inp.uninhabited_fields,
        server_msg_id=inp.server_msg_id,
        msg_date_text=inp.msg_date_text,
        raw_text=inp.raw_text,
    )


__all__ = [
    "T0",
    "build_game_state",
    "galaxy_slot_specs",
    "inbox_message_batches",
    "spy_inputs",
    "to_combat_slots",
    "to_legacy_messages",
    "to_legacy_spy",
    "to_planet_inputs",
]
