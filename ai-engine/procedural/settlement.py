"""Settlement data model — buildings, NPCs with schedules, relationship graph, religions.

Inspired by the Fantasy Town Generator architecture (not its cloud service).
The model is structured, queryable, persistable, and extensible.
"""

import random
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple


# ─── Time slots ─────────────────────────────────────────────────────────────

class TimeSlot:
    MORNING = "morning"
    AFTERNOON = "afternoon"
    DUSK = "dusk"
    EVENING = "evening"
    NIGHT = "night"

    HOURS = {
        "morning": (6, 12),
        "afternoon": (12, 18),
        "dusk": (18, 21),
        "evening": (21, 1),
        "night": (1, 6),
    }

    @classmethod
    def for_hour(cls, hour: int) -> str:
        if 6 <= hour < 12:
            return cls.MORNING
        elif 12 <= hour < 18:
            return cls.AFTERNOON
        elif 18 <= hour < 21:
            return cls.DUSK
        elif hour >= 21 or hour < 1:
            return cls.EVENING
        else:
            return cls.NIGHT

    @classmethod
    def all_slots(cls) -> List[str]:
        return [cls.MORNING, cls.AFTERNOON, cls.DUSK, cls.EVENING, cls.NIGHT]


# ─── Building types ─────────────────────────────────────────────────────────

class BuildingType:
    RESIDENCE = "residence"
    TAVERN = "tavern"
    SHOP = "shop"
    TEMPLE = "temple"
    GOVERNMENT = "government"
    MILITARY = "military"
    MARKET = "market"
    GUILD = "guild"
    INN = "inn"
    TAVERN_TAPROOM = "tavern_taproom"
    TAVERN_PRIVATES = "tavern_privates"
    WELL = "well"
    GATEHOUSE = "gatehouse"
    HOSPITAL = "hospital"
    BARRACKS = "barracks"
    PRISON = "prison"
    MANSION = "mansion"
    FARM = "farm"
    WORKSHOP = "workshop"
    LIBRARY = "library"
    ALCHEMIST = "alchemist"
    BATHS = "baths"
    THEATRE = "theatre"
    ARENA = "arena"
    BAKERY = "bakery"
    BREWERY = "brewery"
    BUTCHER = "butcher"
    GROOMING = "grooming"
    HERBALIST = "herbalist"
    JEWELER = "jeweler"
    LEATHERWORKER = "leatherworker"
    TAILOR = "tailor"
    FOUNTAIN = "fountain"
    STATUE = "statue"
    WATCHTOWER = "watchtower"
    STABLES = "stables"
    MERCHANTS = "merchants"
    WEAPONS = "weapons"
    ARMORY = "armory"
    ALCHEMY = "alchemy"
    MAGIC = "magic"

    ALL = [
        RESIDENCE, TAVERN, SHOP, TEMPLE, GOVERNMENT, MILITARY, MARKET,
        GUILD, INN, WELL, GATEHOUSE, HOSPITAL, BARRACKS, PRISON, MANSION,
        FARM, LIBRARY, BATHS, THEATRE, ARENA, BAKERY, BREWERY, BUTCHER,
        HERBALIST, JEWELER, STABLES, ARMORY, ALCHEMY, MAGIC,
    ]


class ServiceType:
    FOOD_DRINK = "food_and_drink"
    GROOMING = "grooming"
    HEALTH = "health"
    RECREATION = "recreation"
    TRAVEL = "travel"
    RATES = "rates"
    WEAVER = "weaver"
    BLACKSMITH = "blacksmith"
    BAKERY = "bakery"
    BREWING = "brewing"
    BUTCHER = "butcher"
    HERBS = "herbs"
    LEATHER = "leather"
    LIBRARY = "library"
    MAGIC = "magic"
    SURPLUS = "surplus"
    WEAPONS = "weapons"
    JEWELER = "jeweler"
    RELIGION = "religion"
    GOVERNMENT = "government"
    MILITARY = "military"
    PRISON = "prison"
    HOSPITAL = "hospital"
    BATHS = "baths"
    THEATRE = "theatre"
    ARENA = "arena"
    GUILD = "guild"
    STABLES = "stables"

    ALL = [
        FOOD_DRINK, GROOMING, HEALTH, RECREATION, TRAVEL, RATES,
        WEAVER, BLACKSMITH, BAKERY, BREWING, BUTCHER, HERBS,
        LEATHER, LIBRARY, MAGIC, SURPLUS, WEAPONS, JEWELER,
        RELIGION, GOVERNMENT, MILITARY, PRISON, HOSPITAL, BATHS,
        THEATRE, ARENA, GUILD, STABLES,
    ]

    BUILDING_SERVICE_MAP = {
        "tavern": [FOOD_DRINK, BREWING],
        "inn": [FOOD_DRINK, RATES],
        "shop": [SURPLUS],
        "temple": [RELIGION],
        "government": [GOVERNMENT],
        "military": [MILITARY],
        "market": [FOOD_DRINK, WEAVER, BLACKSMITH, BAKERY, BUTCHER, JEWELER],
        "guild": [GUILD],
        "bakery": [BAKERY],
        "brewery": [BREWING],
        "butcher": [BUTCHER],
        "grooming": [GROOMING],
        "herbalist": [HERBS, HEALTH],
        "jeweler": [JEWELER],
        "leatherworker": [LEATHER],
        "tailor": [WEAVER],
        "librarian": [LIBRARY],
        "magic": [MAGIC],
        "weapons": [WEAPONS],
        "well": [HEALTH],
        "fountain": [HEALTH],
        "hospital": [HEALTH, HOSPITAL],
        "baths": [BATHS],
        "theatre": [THEATRE, RECREATION],
        "arena": [ARENA, RECREATION],
        "residence": [],
        "farm": [FOOD_DRINK],
        "stable": [STABLES],
        "gatehouse": [MILITARY],
        "prison": [PRISON],
        "mansion": [GOVERNMENT],
        "armory": [WEAPONS],
        "alchemy": [MAGIC, HERBS],
        "watchtower": [MILITARY],
    }


# ─── Occupations ────────────────────────────────────────────────────────────

class OccupationType:
    ADMIRAL = "admiral"
    ALCHEMIST = "alchemist"
    ARMORER = "armorer"
    BLACKSMITH = "blacksmith"
    BUTCHER = "butcher"
    CARTOGRAPHER = "cartographer"
    COACHMAN = "coachman"
    COOK = "cook"
    CRIMINAL = "criminal"
    FARMER = "farmer"
    FORTIFIER = "fortifier"
    GATEKEEPER = "gatekeeper"
    GOVERNOR = "governor"
    GUARD = "guard"
    HEALER = "healer"
    HERBALIST = "herbalist"
    INNKEEPER = "innkeeper"
    JEWELER = "jeweler"
    LIBRARIAN = "librarian"
    MERCHANT = "merchant"
    MILITARY = "military"
    PRIEST = "priest"
    TAVERNKEEPER = "tavernkeeper"
    UNEMPLOYED = "unemployed"
    WEAVER = "weaver"
    WOODCHOPPER = "woodchopper"
    SCHOLAR = "scholar"
    MAGE = "mage"
    BAKER = "baker"
    BREWER = "brewer"
    DOCTOR = "doctor"
    ACTOR = "actor"
    ATHLETE = "athlete"
    BUILDER = "builder"
    COACHMAN = "coachman"
    PAGE = "page"
    PRISONER = "prisoner"
    SQUIRE = "squire"
    SERVANT = "servant"
    RELIGIOUS = "religious"

    ALL = [
        ADMIRAL, ALCHEMIST, ARMORER, BLACKSMITH, BUTCHER, CARTOGRAPHER,
        COACHMAN, COOK, CRIMINAL, FARMER, FORTIFIER, GATEKEEPER,
        GOVERNOR, GUARD, HEALER, HERBALIST, INNKEEPER, JEWELER,
        LIBRARIAN, MERCHANT, MILITARY, PRIEST, TAVERNKEEPER, UNEMPLOYED,
        WEAVER, WOODCHOPPER, SCHOLAR, MAGE, BAKER, BREWER, DOCTOR,
        ACTOR, ATHLETE, BUILDER, PAGE, PRISONER, SQUIRE, SERVANT, RELIGIOUS,
    ]

    # Map building types to typical occupations
    BUILDING_OCCUPATION_MAP = {
        "tavern": ["tavernkeeper", "cook", "singer"],
        "inn": ["innkeeper", "cook", "cook", "page"],
        "temple": ["priest", "religious"],
        "market": ["merchant", "butcher", "baker", "weaver", "jeweler"],
        "bakery": ["baker", "cook"],
        "brewery": ["brewer", "cook"],
        "butcher": ["butcher", "cook"],
        "herbalist": ["herbalist", "healer"],
        "hospital": ["doctor", "healer"],
        "armory": ["armorer", "blacksmith"],
        "weapons": ["armorer", "blacksmith"],
        "library": ["librarian", "scholar"],
        "alchemy": ["alchemist", "mage"],
        "government": ["governor", "guard"],
        "mansion": ["governor", "servant", "guard"],
        "barracks": ["military", "guard"],
        "arena": ["actor", "athlete"],
        "theatre": ["actor", "athlete"],
        "baths": ["bathkeeper", "grooming"],
        "stable": ["groom", "coachman"],
        "shop": ["merchant"],
        "residence": ["unemployed"],
        "farm": ["farmer"],
    }


# ─── Religion ───────────────────────────────────────────────────────────────

@dataclass
class Religion:
    name: str
    symbol: str
    primary_attributes: List[str]
    alignment: str
    description: str
    priests: List[str] = field(default_factory=list)
    holidays: List[Dict[str, str]] = field(default_factory=list)
    clergy_title: str = "Priest"
    clergy_gender: str = "female"
    faith_level: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Schedule ───────────────────────────────────────────────────────────────

@dataclass
class ScheduleEntry:
    time_slot: str
    location: str
    activity: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time_slot": self.time_slot,
            "location": self.location,
            "activity": self.activity,
            "notes": self.notes,
        }


@dataclass
class NPCSchedule:
    npc_name: str
    entries: List[ScheduleEntry] = field(default_factory=list)

    def add_entry(self, time_slot: str, location: str, activity: str, notes: str = ""):
        self.entries.append(ScheduleEntry(time_slot, location, activity, notes))

    def get_at_time(self, time_slot: str) -> Optional[ScheduleEntry]:
        for entry in self.entries:
            if entry.time_slot == time_slot:
                return entry
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "npc_name": self.npc_name,
            "entries": [e.to_dict() for e in self.entries],
        }

    def location_at_time(self, time_slot: str) -> Optional[str]:
        entry = self.get_at_time(time_slot)
        return entry.location if entry else None


# ─── Relationships ──────────────────────────────────────────────────────────

class RelationshipType:
    ALLY = "ally"
    ENEMY = "enemy"
    LOVE = "love"
    RIVAL = "rival"
    MENTOR = "mentor"
    FAMILY = "family"
    FRIEND = "friend"
    FOE = "foe"
    SUBORDINATE = "subordinate"
    EMPLOYER = "employer"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"
    RIVALRY = "rivalry"
    CRUSH = "crush"
    ARCH_NEMESIS = "arch_nemesis"

    ALL = [
        ALLY, ENEMY, LOVE, RIVAL, MENTOR, FAMILY, FRIEND, FOE,
        SUBORDINATE, EMPLOYER, NEUTRAL, UNKNOWN, RIVALRY, CRUSH,
        ARCH_NEMESIS,
    ]

    POSITIVE = {ALLY, LOVE, FRIEND, MENTOR, EMPLOYER, CRUSH, FAMILY}
    NEGATIVE = {ENEMY, RIVAL, FOE, RIVALRY, ARCH_NEMESIS}
    NEUTRAL = {NEUTRAL, UNKNOWN, SUBORDINATE}


@dataclass
class TypedRelationship:
    source: str
    target: str
    relationship_type: str
    strength: float = 0.5
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relationship_type": self.relationship_type,
            "strength": self.strength,
            "description": self.description,
        }


# ─── Building ───────────────────────────────────────────────────────────────

@dataclass
class Building:
    name: str
    building_type: str
    services: List[str] = field(default_factory=list)
    description: str = ""
    occupants: List[str] = field(default_factory=list)
    inventory: List[Dict[str, Any]] = field(default_factory=list)
    schedule: Dict[str, str] = field(default_factory=dict)
    district: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.building_type,
            "services": self.services,
            "description": self.description,
            "occupants": self.occupants,
            "inventory": self.inventory,
            "schedule": self.schedule,
            "district": self.district,
            "notes": self.notes,
        }

    def has_service(self, service: str) -> bool:
        return service in self.services

    def occupant_at_time(self, time_slot: str) -> List[str]:
        """Return NPC names present at this building during a time slot."""
        return [name for name in self.occupants if name in self.schedule
                and self.schedule.get(name, {}).get(time_slot, "") == self.name]


# ─── Settlement NPC ─────────────────────────────────────────────────────────

@dataclass
class SettlementNPC:
    name: str
    occupation: str
    race: str = "human"
    age: int = 30
    description: str = ""
    personality: List[str] = field(default_factory=list)
    relationships: List[TypedRelationship] = field(default_factory=list)
    schedule: Optional[NPCSchedule] = None
    building: str = ""
    alignment: str = "neutral"
    notes: str = ""
    faction: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "occupation": self.occupation,
            "race": self.race,
            "age": self.age,
            "description": self.description,
            "personality": self.personality,
            "building": self.building,
            "alignment": self.alignment,
            "notes": self.notes,
            "faction": self.faction,
        }
        if self.schedule:
            result["schedule"] = self.schedule.to_dict()
        if self.relationships:
            result["relationships"] = [r.to_dict() for r in self.relationships]
        return result

    def find_at_time(self, time_slot: str) -> str:
        """Return the building name where this NPC is during a time slot."""
        if self.schedule:
            return self.schedule.location_at_time(time_slot)
        return self.building or "unknown"


# ─── Settlement ─────────────────────────────────────────────────────────────

class SettlementSize:
    HAMLET = "hamlet"
    VILLAGE = "village"
    TOWN = "town"
    CITY = "city"
    METROPOLIS = "metropolis"

    POP_RANGE = {
        HAMLET: (50, 200),
        VILLAGE: (200, 2000),
        TOWN: (2000, 10000),
        CITY: (10000, 100000),
        METROPOLIS: (100000, 500000),
    }


@dataclass
class Settlement:
    name: str
    size: str  # SettlementSize value
    description: str = ""
    buildings: List[Building] = field(default_factory=list)
    npcs: List[SettlementNPC] = field(default_factory=list)
    religions: List[Religion] = field(default_factory=list)
    factions: List[Dict[str, Any]] = field(default_factory=list)
    districts: List[Dict[str, Any]] = field(default_factory=list)
    economy: Dict[str, Any] = field(default_factory=dict)
    government: Dict[str, Any] = field(default_factory=dict)

    def add_building(self, building: Building):
        self.buildings.append(building)

    def add_npc(self, npc: SettlementNPC):
        self.npcs.append(npc)

    def add_religion(self, religion: Religion):
        self.religions.append(religion)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "description": self.description,
            "buildings": [b.to_dict() for b in self.buildings],
            "npcs": [n.to_dict() for n in self.npcs],
            "religions": [r.to_dict() for r in self.religions],
            "factions": self.factions,
            "districts": self.districts,
            "economy": self.economy,
            "government": self.government,
        }

    def query_at_time(self, time_slot: str, location: Optional[str] = None,
                      building_type: Optional[str] = None) -> List[SettlementNPC]:
        """Find NPCs at a specific location and time.

        Args:
            time_slot: TimeSlot value (morning, afternoon, dusk, evening, night)
            location: Building name to filter by (None = anywhere)
            building_type: Building type to filter by (None = anywhere)

        Returns:
            List of NPCs who are present at the specified time and place.
        """
        results = []
        for npc in self.npcs:
            npc_location = npc.find_at_time(time_slot)
            if location and npc_location != location:
                continue
            if building_type:
                # Find which building the NPC is at
                building = self._find_building(npc_location)
                if building and building.building_type != building_type:
                    continue
            results.append(npc)
        return results

    def query_building(self, building_name: str) -> Optional[Building]:
        """Find a building by name."""
        for b in self.buildings:
            if b.name == building_name:
                return b
        return None

    def query_building_by_service(self, service: str, time_slot: Optional[str] = None) -> List[Building]:
        """Find buildings that provide a service, optionally at a time."""
        results = []
        for b in self.buildings:
            if service in b.services:
                if time_slot is None:
                    results.append(b)
                else:
                    # Check if anyone relevant is here at this time
                    occupants_here = [
                        n.name for n in self.npcs
                        if n.find_at_time(time_slot) == b.name
                    ]
                    results.append(b)  # Always include if it provides the service
        return results

    def query_relationships(self, npc_name: str,
                            rel_type: Optional[str] = None) -> List[TypedRelationship]:
        """Find all relationships for an NPC, optionally filtered by type."""
        for npc in self.npcs:
            if npc.name == npc_name:
                if rel_type:
                    return [r for r in npc.relationships if r.relationship_type == rel_type]
                return npc.relationships
        return []

    def _find_building(self, name: str) -> Optional[Building]:
        for b in self.buildings:
            if b.name == name:
                return b
        return None

    def npc_schedule_lookup(self, npc_name: str) -> Optional[NPCSchedule]:
        """Get the full schedule for an NPC by name."""
        for npc in self.npcs:
            if npc.name == npc_name and npc.schedule:
                return npc.schedule
        return None

    def time_summary(self, time_slot: str) -> Dict[str, List[str]]:
        """Get a summary of who is where at a given time.

        Returns dict mapping building name -> list of NPC names present.
        """
        summary: Dict[str, List[str]] = {}
        for npc in self.npcs:
            location = npc.find_at_time(time_slot)
            if location:
                if location not in summary:
                    summary[location] = []
                summary[location].append(npc.name)
        return summary
