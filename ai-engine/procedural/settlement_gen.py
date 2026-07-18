"""Settlement generator — create structured, populated settlements.

Inspired by Fantasy Town Generator's architecture: buildings are created first
with their residents, dependencies are resolved, then relationships and
schedules are generated. NOT using FTG's cloud service — this is fully local.
"""

import random
from typing import Any, Dict, List, Optional, Set, Tuple

from procedural.settlement import (
    Building, BuildingType, OccupationType, Religion, ScheduleEntry,
    Settlement, SettlementNPC, SettlementSize, ServiceType,
    TimeSlot, TypedRelationship, RelationshipType, NPCSchedule,
)

# ─── Name pools ─────────────────────────────────────────────────────────────

SETTLEMENT_NAME_PREFIXES = [
    "New", "Old", "Fort", "Port", "West", "East", "North", "South",
    "Upper", "Lower", "Little", "Great", "Black", "White", "Red",
    "Green", "Stone", "River", "Mountain", "Forest", "Hill",
]

SETTLEMENT_NAME_SUFFIXES = [
    "haven", "port", "ton", "stead", "field", "dale", "ridge",
    "ford", "bridge", "castle", " Keep", " vale", "shire", "mere",
    "heim", "gard", "holm", "wick", "tond", "mere",
]

BUILDING_NAMES_TAPROOM = [
    "The Prancing Pony", "The Flaming Flask", "The Rusty Anchor",
    "The Golden Griffin", "The Silver Sword", "The Sleeping Dragon",
    "The Broken Wheel", "The Crown & Thistle", "The Howling Wolf",
    "The Blind Bear", "The Silver Swan", "The Gilded Goblet",
    "The Drowned Rat", "The Crow & Key", "The Lucky Duck",
    "The Iron Cauldron", "The Mellow Mule", "The Weary Wyvern",
    "The Rusty Anchor", "The Crimson Cap", "The Wandering Minstrel",
    "The Silver Stag", "The Weeping Willow", "The Golden Fleece",
    "The Rusty Hook", "The Broken Oar", "The Singing Shovel",
    "The Silver Salver", "The Crimson Crane", "The Golden Gander",
    "The Wandering Minstrel", "The Silver Stag", "The Weeping Willow",
    "The Golden Fleece", "The Rusty Hook", "The Broken Oar",
]

BUILDING_NAMES_SHOP = [
    "Martikov's Goods", "The Gilded Cage", "The Silver Scale",
    "Gavony's General Store", "The Copper Kettle", "Renshun's Oddments",
    "The Silver Swan", "The Brass Compass", "The Iron Thimble",
    "Wren's Wares", "The Gilded Griffin", "The Copper Coin",
    "The Silver Stag", "The Golden Goose", "The Rusty Nail",
    "The Wandering Merchant", "The Silver Salver", "The Crimson Cap",
]

BUILDING_NAMES_TEMPLE = [
    "The Silver Sanctuary", "The Temple of Light", "The Sacred Grove",
    "The Dawn Cathedral", "The Sanctuary of Mercy", "The House of the Sun",
    "The Temple of the Moon", "The Shrine of Stars", "The Chapel of Winds",
    "The Abbey of Silence", "The Cathedral of Dawn", "The Chapel of the Hearth",
    "The Temple of Storms", "The Temple of the Deep", "The Shrine of Ashes",
]

BUILDING_NAMES_GOV = [
    "The Iron Hall", "The Council Chambers", "The Governor's Palace",
    "The Court of Justice", "The Assembly Hall", "The Governor's Manor",
    "The Town Hall", "The Court of the Crown", "The Seat of Power",
    "The Hall of Records", "The Citadel", "The Governor's Keep",
]

BUILDING_NAMES_FARM = [
    "The Green Pastures", "Sunfield Farm", "The Happy Harvest",
    "Miller's Farm", "Oakridge Farm", "The Golden Wheat",
    "Riverbend Farm", "The Shepherd's Rest", "Stonecrop Farm",
    "The Lazy Cow", "The content Pig", "The content Chicken",
    "Hilltop Farm", "Meadowlark Farm", "The content Cow",
]

# ─── NPC name pools ────────────────────────────────────────────────────────

MALE_NAMES = [
    "Aldous", "Bram", "Corbin", "Doren", "Elliot", "Finnan", "Garrick",
    "Haldir", "Ivor", "Joren", "Kael", "Lorin", "Merric", "Nolan",
    "Osric", "Percival", "Quint", "Reginald", "Stefan", "Theron",
    "Ulric", "Vance", "Willem", "Xander", "Yoric", "Zane",
    "Aramis", "Balthazar", "Cedric", "Darius", "Edmund", "Fitzwilliam",
    "Godfrey", "Horatio", "Ignatius", "Julian", "Klaus", "Lysander",
    "Marcus", "Nathaniel", "Oliver", "Percy", "Quentin", "Reuben",
    "Sebastian", "Theodore", "Ulrich", "Viktor", "Winston",
]

FEMALE_NAMES = [
    "Ada", "Briar", "Celeste", "Diana", "Elara", "Fiona", "Greta",
    "Helena", "Ingrid", "Julia", "Kendra", "Luna", "Mirabelle",
    "Nadia", "Ophelia", "Petra", "Quinn", "Rosalind", "Svetlana",
    "Tessa", "Ursula", "Vera", "Wanda", "Xena", "Yvette", "Zara",
    "Astrid", "Bianca", "Cordelia", "Dorothea", "Evangeline",
    "Freya", "Giselle", "Hildegarde", "Isadora", "Josephine",
    "Katarina", "Lucretia", "Minerva", "Octavia", "Philomena",
    "Rosalind", "Seraphina", "Theodora", "Urania", "Valentina",
    "Wilhelmina", "Xanthe", "Yasmin", "Zofia",
]

SURNAMES = [
    "Ashford", "Blackwood", "Copperfield", "Dawnstrider", "Emberheart",
    "Frostborn", "Goldleaf", "Hawklight", "Ironforge", "Jasperstone",
    "Kingsley", "Lionheart", "Moonwhisper", "Nightshade", "Oakheart",
    "Proudfoot", "Quickwater", "Ravencrest", "Stoneheart", "Thornfield",
    "Underhill", "Valeward", "Windrunner", "Stormborn", "Firebrand",
    "Brennan", "Connelly", "Drake", "Everett", "Finnegan", "Gallowglass",
    "Hawthorne", "Ironside", "Journeyman", "Kingsley", "Larkspur",
    "Moonstone", "Nightbreeze", "Oakenshield", "Pinecrest", "Quicksilver",
    "Redwood", "Stormwatch", "Thornwall", "Underbrook", "Vance",
    "Wilderman", "Ashenvale", "Brightwater", "Crowley", "Duskfall",
]


# ─── Schedule templates ────────────────────────────────────────────────────

def _default_schedule(npc_name: str, occupation: str, building: str,
                      settlement_name: str) -> List[ScheduleEntry]:
    """Generate a default daily schedule based on occupation and building."""
    entries = []

    # Most working NPCs follow a routine: home -> work -> home
    entries.append(ScheduleEntry("night", "home", f"Sleeping in {building}"))
    entries.append(ScheduleEntry("morning", building, f"Opening {building}", f"Preparing {building} for the day"))
    entries.append(ScheduleEntry("afternoon", building, f"Working at {building}", f"Plying their trade"))
    entries.append(ScheduleEntry("dusk", building, f"Closing {building}", f"Wrapping up for the day"))
    entries.append(ScheduleEntry("evening", building, f"Relaxing at {building}", f"End of the work day"))

    # Tavernkeepers/innkeepers stay later
    if occupation in ("tavernkeeper", "innkeeper"):
        entries[-1] = ScheduleEntry("evening", building, f"Hosting patrons at {building}",
                                     f"Keeping the place lively")
        entries.append(ScheduleEntry("night", building, f"Closing {building}",
                                      f"Locking up after last call"))

    # Priests have morning prayers
    if occupation in ("priest", "religious"):
        entries[1] = ScheduleEntry("morning", building, f"Morning prayers at {building}")
        entries[2] = ScheduleEntry("afternoon", building, f"Counseling / rituals at {building}")

    # Farmers work in the field
    if occupation in ("farmer",):
        entries[1] = ScheduleEntry("morning", building, f"Working the fields")
        entries[2] = ScheduleEntry("afternoon", building, f"Working the fields", f"Under the hot sun")
        entries[3] = ScheduleEntry("dusk", building, f"Returning from fields", f"Carsick with fatigue")

    # Guards/ military have 24h patterns
    if occupation in ("guard", "military", "gatekeeper"):
        entries[0] = ScheduleEntry("night", building, f"Night watch at {building}")
        entries[1] = ScheduleEntry("morning", building, f"Morning patrol")
        entries[2] = ScheduleEntry("afternoon", building, f"Afternoon duties")
        entries[3] = ScheduleEntry("dusk", building, f"Evening watch setup")

    return entries


# ─── Personality templates ─────────────────────────────────────────────────

PERSONALITY_TRAITS = [
    "friendly", "gruff", "shy", "bold", "cunning", "optimistic", "pessimistic",
    "honest", "deceitful", "generous", "greedy", "patient", "hot-tempered",
    "superstitious", "intelligent", "ignorant", "charismatic", "awkward",
    "methodical", "impulsive", "cautious", "reckless", "kind", "cruel",
    "loyal", "treacherous", "pious", "irreverent", "ambitious", "lazy",
]


# ─── Building pool ──────────────────────────────────────────────────────────

def _choose_building_name(btype: str, rng: random.Random) -> str:
    """Generate a plausible name for a building of a given type."""
    if btype in ("tavern", "tavern_taproom", "tavern_privates"):
        return rng.choice(BUILDING_NAMES_TAPROOM)
    elif btype in ("shop", "merchants"):
        return rng.choice(BUILDING_NAMES_SHOP)
    elif btype in ("temple",):
        return rng.choice(BUILDING_NAMES_TEMPLE)
    elif btype in ("government", "mansion"):
        return rng.choice(BUILDING_NAMES_GOV)
    elif btype == "farm":
        return rng.choice(BUILDING_NAMES_FARM)
    else:
        # Generic names
        prefix = rng.choice(["The", "Old", "New", "Great", "Little"])
        suffix = rng.choice(["Hall", "House", "Place", "Corner", "Way", "Gate"])
        return f"{prefix} {suffix}"


def _get_services(btype: str) -> List[str]:
    """Get services for a building type."""
    return ServiceType.BUILDING_SERVICE_MAP.get(btype, [])


def _get_occupations(btype: str) -> List[str]:
    """Get typical occupations for a building type."""
    return OccupationType.BUILDING_OCCUPATION_MAP.get(btype, [])


# ─── Settlement Generator ──────────────────────────────────────────────────

class SettlementGenerator:
    """Generate a complete, structured settlement."""

    def __init__(self, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()

    def generate(self, name: str, size: str, num_npcs: int = 0,
                 num_buildings: int = 0) -> Settlement:
        """Generate a complete settlement.

        Args:
            name: Settlement name
            size: SettlementSize value (hamlet, village, town, city)
            num_npcs: Override number of NPCs (default: derived from size)
            num_buildings: Override number of buildings (default: derived from size)
        """
        pop_min, pop_max = SettlementSize.POP_RANGE.get(size, (200, 2000))
        if num_npcs == 0:
            num_npcs = self.rng.randint(pop_min // 4, pop_max // 4)
        if num_buildings == 0:
            num_buildings = self._building_count_for_size(size)

        settlement = Settlement(
            name=name,
            size=size,
            description=f"A {size} named {name}",
        )

        # 1. Create buildings
        buildings_created = self._create_buildings(settlement, num_buildings)

        # 2. Create NPCs and assign to buildings
        self._create_npcs(settlement, buildings_created, num_npcs)

        # 3. Generate relationships
        self._generate_relationships(settlement)

        # 4. Generate schedules
        self._generate_schedules(settlement, name)

        # 5. Add religions if appropriate
        self._add_religions(settlement)

        return settlement

    def _building_count_for_size(self, size: str) -> int:
        counts = {
            SettlementSize.HAMLET: 8,
            SettlementSize.VILLAGE: 25,
            SettlementSize.TOWN: 50,
            SettlementSize.CITY: 120,
            SettlementSize.METROPOLIS: 300,
        }
        return counts.get(size, 25)

    def _create_buildings(self, settlement: Settlement, count: int) -> List[Building]:
        """Create buildings and return them for NPC assignment."""
        # Determine building types based on size
        btypes = self._building_types_for_size(settlement.size)
        used_names: Set[str] = set()

        for _ in range(count):
            btype = self.rng.choice(btypes)
            name = self._unique_name(btype, used_names)
            used_names.add(name)

            services = _get_services(btype)
            building = Building(
                name=name,
                building_type=btype,
                services=services,
                description=self._building_description(btype),
            )

            # Assign a district
            building.district = self.rng.choice(["downtown", "residential",
                                                   "industrial", "temple",
                                                   "market", "slums",
                                                   "fortress", "suburbs"])

            settlement.add_building(building)

        return settlement.buildings

    def _building_types_for_size(self, size: str) -> List[str]:
        """Pick building types weighted by settlement size."""
        if size == SettlementSize.HAMLET:
            return ["residence", "residence", "tavern", "farm", "farm",
                    "shop", "temple", "well", "barracks"]
        elif size == SettlementSize.VILLAGE:
            return ["residence", "residence", "tavern", "tavern", "inn",
                    "farm", "farm", "shop", "shop", "temple", "temple",
                    "bakery", "butcher", "market", "stable", "armory"]
        elif size == SettlementSize.TOWN:
            return ["residence", "residence", "residence", "tavern", "tavern",
                    "inn", "inn", "farm", "farm", "shop", "shop", "shop",
                    "temple", "temple", "bakery", "butcher", "brewery",
                    "herbalist", "jeweler", "market", "guild", "hospital",
                    "armory", "stable", "library", "theatre", "arena"]
        else:
            return BuildingType.ALL

    def _unique_name(self, btype: str, used: Set[str]) -> str:
        """Generate a unique building name."""
        for _ in range(20):
            name = _choose_building_name(btype, self.rng)
            if name not in used:
                return name
        # Fallback with counter
        counter = len(used)
        return f"{btype.title()} #{counter}"

    def _building_description(self, btype: str) -> str:
        """Generate a brief description for a building type."""
        desc_map = {
            "residence": "A modest home with a thatched roof and wooden frame",
            "tavern": "A bustling tavern with a large common room, bar, and private booths",
            "inn": "A two-story inn with a warm common room and upper-floor rooms",
            "shop": "A small shop with display windows and a back room",
            "temple": "A grand stone temple with stained glass windows",
            "bakery": "A warm bakery with the scent of fresh bread",
            "brewery": "A brewery with copper vats and barrels lining the walls",
            "butcher": "A butcher's shop with hooks and cutting tables",
            "herbalist": "A cozy herbalist's shop with dried herbs hanging from the ceiling",
            "jeweler": "A jeweler's shop with glass display cases",
            "market": "An open-air market with stalls and awnings",
            "guild": "A guildhall with a large meeting hall and trophy wall",
            "hospital": "A hospital with healing wards and an apothecary",
            "armory": "An armory with weapons on the walls and a smithy in back",
            "stable": "A stable with stalls and a tack room",
            "library": "A library with shelves of books and reading nooks",
            "theatre": "A theatre with a stage and rows of seats",
            "arena": "An arena with a sand floor and spectator stands",
            "farm": "A farm with fields, barns, and a farmhouse",
            "well": "A stone well with a wooden roof",
            "barracks": "A barracks with bunk beds and a muster yard",
            "market": "A market square with stalls and a fountain",
        }
        return desc_map.get(btype, f"A {btype} building")

    def _create_npcs(self, settlement: Settlement, buildings: List[Building],
                     count: int):
        """Create NPCs and assign them to buildings."""
        for i in range(count):
            name = self._unique_npc_name()
            # Assign occupation based on available buildings
            occupation = self._pick_occupation(buildings)
            # Pick a random building to live/work in
            building = self.rng.choice(buildings) if buildings else None
            building_name = building.name if building else "unknown"

            npc = SettlementNPC(
                name=name,
                occupation=occupation,
                race=self.rng.choice(["human", "human", "human", "elf", "dwarf",
                                       "halfling", "gnome"]),
                age=self.rng.randint(18, 70),
                description=f"A {occupation} in {settlement.name}",
                personality=self.rng.sample(PERSONALITY_TRAITS, self.rng.randint(1, 3)),
                building=building_name,
                alignment=self.rng.choice(["lawful good", "neutral good",
                                            "chaotic good", "lawful neutral",
                                            "true neutral", "chaotic neutral",
                                            "lawful evil", "neutral evil",
                                            "chaotic evil"]),
            )

            if building:
                npc.relationships = []
                building.occupants.append(name)

            settlement.add_npc(npc)

    def _unique_npc_name(self) -> str:
        """Generate a unique NPC name."""
        first = self.rng.choice(MALE_NAMES + FEMALE_NAMES)
        last = self.rng.choice(SURNAMES)
        return f"{first} {last}"

    def _pick_occupation(self, buildings: List[Building]) -> str:
        """Pick an occupation weighted by available buildings."""
        # Count building types
        btype_counts: Dict[str, int] = {}
        for b in buildings:
            btype_counts[b.building_type] = btype_counts.get(b.building_type, 0) + 1

        # Weight occupations by building counts
        weighted = []
        for btype, count in btype_counts.items():
            for occ in OccupationType.BUILDING_OCCUPATION_MAP.get(btype, []):
                weighted.extend([occ] * count)

        if weighted:
            return self.rng.choice(weighted)
        return self.rng.choice(OccupationType.ALL[:20])  # Default to common

    def _generate_relationships(self, settlement: Settlement):
        """Generate typed relationships between NPCs."""
        npcs = settlement.npcs
        if len(npcs) < 2:
            return

        # Give some NPCs relationships
        rel_count = max(1, len(npcs) // 3)
        if len(npcs) < 2:
            return
        for _ in range(rel_count):
            npc_a = self.rng.choice(npcs)
            npc_b = self.rng.choice([n for n in npcs if n.name != npc_a.name])
            rel_type = self.rng.choice(RelationshipType.ALL)
            strength = self.rng.uniform(0.3, 1.0)

            rel = TypedRelationship(
                source=npc_a.name,
                target=npc_b.name,
                relationship_type=rel_type,
                strength=strength,
                description=self._rel_description(rel_type),
            )
            npc_a.relationships.append(rel)

    def _rel_description(self, rel_type: str) -> str:
        """Generate a flavor description for a relationship type."""
        descs = {
            "ally": "Strong allies in business",
            "enemy": "Open enemies with a long history",
            "love": "Deep in love",
            "rival": "Competing for the same goal",
            "mentor": "A respected teacher-student bond",
            "family": "Blood relatives",
            "friend": "Good friends",
            "foe": "Grudge-holding adversaries",
            "subordinate": "Master and servant",
            "employer": "Employer-employee relationship",
            "neutral": "No strong feelings either way",
            "unknown": "Unconfirmed relationship",
            "rivalry": "Professional rivalry",
            "crush": "Secret admiration",
            "arch_nemesis": "Lifelong bitter enemies",
        }
        return descs.get(rel_type, f"{rel_type} relationship")

    def _generate_schedules(self, settlement: Settlement, name: str):
        """Generate daily schedules for all NPCs."""
        for npc in settlement.npcs:
            schedule_entries = _default_schedule(
                npc.name, npc.occupation, npc.building, name
            )
            schedule = NPCSchedule(npc_name=npc.name)
            for entry in schedule_entries:
                schedule.add_entry(
                    entry.time_slot, entry.location, entry.activity, entry.notes
                )
            npc.schedule = schedule

    def _add_religions(self, settlement: Settlement):
        """Add 1-2 religions to the settlement."""
        num_religions = self.rng.randint(1, 2)
        for _ in range(num_religions):
            rel_name = self.rng.choice([
                "The Dawn Father", "The Moon Mother", "The Earth Mother",
                "The Storm Lord", "The Firebrand", "The Silent One",
                "The Golden Sun", "The Silver Crescent", "The Eternal Flame",
                "The Deep One", "The Wind Walker", "The Stone Father",
                "The Shadow Queen", "The Lightbringer", "The World Tree",
            ])
            religion = Religion(
                name=rel_name,
                symbol=self.rng.choice(["☀", "☽", "⚕", "✝", "🜏", "🜁", "🜂", "🜃"]),
                primary_attributes=self.rng.sample(
                    ["Strength", "Dexterity", "Constitution", "Intelligence",
                     "Wisdom", "Charisma"], self.rng.randint(1, 2)),
                alignment=self.rng.choice(["chaotic good", "lawful neutral",
                                            "true neutral", "chaotic evil",
                                            "lawful good"]),
                description=f"The {rel_name} is a major faith in {settlement.name}.",
                faith_level=self.rng.randint(1, 4),
            )
            settlement.add_religion(religion)
