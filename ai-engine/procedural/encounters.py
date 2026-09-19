"""Random encounter generation."""

import random
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class GeneratedEncounter:
    """A procedurally generated encounter."""
    name: str
    description: str
    monsters: List[Dict]
    difficulty: str
    environment: str
    hooks: List[str]
    treasure_cr: float


class EncounterGenerator:
    """Generate random encounters."""

    # Monster types by CR
    MONSTERS_BY_CR = {
        0.25: ["Goblin", "Skeleton", "Bandit"],
        0.5: ["Orc", "Dire Wolf", "Harpy"],
        1: ["Ghoul", "Bugbear", "Wererat"],
        2: ["Ogre", "Gargoyle", "Wyvern"],
        3: ["Manticore", "Giant Spider", "Troll"],
        4: ["Fire Elemental", "Chimera", "Helmed Horror"],
        5: ["Demon", "Devil", "Beholder"],
    }

    ENVIRONMENTS = [
        "Forest clearing",
        "Mountain pass",
        "Dungeon corridor",
        "Coastal cave",
        "Underground cavern",
        "Ancient ruin",
        "Swamp marsh",
        "Tower chamber",
        "Castle courtyard",
        "Abandoned village",
    ]

    def __init__(self):
        pass

    def generate(
        self, difficulty: str, party_level: int, party_size: int = 4
    ) -> GeneratedEncounter:
        """Generate a random encounter."""
        from combat.difficulty import EncounterDifficulty

        difficulty_enum = EncounterDifficulty[difficulty.upper()]

        monsters = self._generate_monster_list(difficulty_enum, party_level, party_size)
        environment = random.choice(self.ENVIRONMENTS)

        # Create encounter. The band reported is the one the roster actually
        # rates at: MONSTERS_BY_CR cannot always reach the one asked for, and
        # echoing the request back would misreport it to the caller.
        encounter = GeneratedEncounter(
            name=self._generate_name(environment),
            description=self._generate_description(environment, monsters),
            monsters=monsters,
            difficulty=self._rate(monsters, party_level, party_size),
            environment=environment,
            hooks=self._generate_hooks(environment, len(monsters)),
            treasure_cr=sum(m.get("cr", 0) * m.get("count", 1) for m in monsters),
        )

        return encounter

    # Ascending, so a band's upper bound is the next one's threshold.
    _BANDS = ["trivial", "easy", "medium", "hard", "deadly"]
    _MAX_MONSTERS = 12

    def _generate_monster_list(
        self, difficulty, party_level: int, party_size: int
    ) -> List[Dict]:
        """Build a roster whose XP lands inside the party's budget for `difficulty`.

        This used to size the encounter from `party_level * party_size *
        0.5/1.0/1.5`, which is not a 5e quantity, and it compared an
        EncounterDifficulty member against the string "easy" — never equal,
        so every encounter took the 1.5 branch and the requested band had no
        effect at all. The party composition it built went unused.

        Adding a monster raises both the XP sum and the DMG count multiplier,
        so the reachable totals are lumpy and filling greedily undershoots.
        Search each roster size instead: for n monsters the multiplier is
        fixed, which turns the question into "n challenge ratings summing
        into this window" and finds a fit whenever one exists.

        Returns the closest roster it can when the band is out of reach —
        MONSTERS_BY_CR bottoms out at CR 1/4, which alone can outweigh a
        whole band for a solo level-1 character. generate() reports the band
        actually achieved rather than the one requested.
        """
        from combat.difficulty import XP_BY_CR, DynamicDifficulty

        engine = DynamicDifficulty()
        party = engine.get_party_composition(party_size, float(party_level))
        budget = engine.party_budget(party)

        target = budget[difficulty.value]
        above = self._BANDS.index(difficulty.value) + 1
        ceiling = budget[self._BANDS[above]] if above < len(self._BANDS) else float("inf")

        available = sorted(self.MONSTERS_BY_CR)
        rating = party.party_power_rating

        chosen = None
        for count in range(1, self._MAX_MONSTERS + 1):
            multiplier = engine.encounter_multiplier(count)
            fit = self._crs_summing_into(
                count,
                low=target * rating / multiplier,
                high=ceiling * rating / multiplier,
                available=available,
            )
            if fit:
                chosen = fit
                break

        if chosen is None:
            # No roster lands in the band. Fall to whichever end we missed:
            # one cheapest monster when even that overshoots (a solo level-1
            # character is outweighed by a single CR 1/4), and the largest
            # roster when the band is out of reach upward — MONSTERS_BY_CR
            # stops at CR 5, which cannot threaten a high-level party.
            smallest = engine.encounter_multiplier(1) * XP_BY_CR[available[0]] / rating
            if smallest >= ceiling:
                chosen = [available[0]]
            else:
                chosen = [available[-1]] * self._MAX_MONSTERS

        random.shuffle(chosen)
        monsters: List[Dict] = []
        for cr in sorted(set(chosen)):
            monsters.append({
                "name": random.choice(self.MONSTERS_BY_CR[cr]),
                "cr": cr,
                "count": chosen.count(cr),
            })
        return monsters

    @staticmethod
    def _crs_summing_into(
        count: int, low: float, high: float, available: List[float]
    ) -> Optional[List[float]]:
        """`count` challenge ratings whose XP sums to at least `low` and under
        `high`, or None when no such roster exists at this size.

        Starts every slot at the cheapest monster and upgrades one slot at a
        time, taking the largest upgrade that stays under `high`.
        """
        from combat.difficulty import XP_BY_CR

        crs = [available[0]] * count
        total = XP_BY_CR[available[0]] * count
        if total >= high:
            return None
        if total >= low:
            return crs

        for slot in range(count):
            for cr in reversed(available):
                delta = XP_BY_CR[cr] - XP_BY_CR[crs[slot]]
                if delta > 0 and total + delta < high:
                    crs[slot] = cr
                    total += delta
                    break
            if total >= low:
                return crs
        return None

    @staticmethod
    def _rate(monsters: List[Dict], party_level: int, party_size: int) -> str:
        """The band this roster actually lands in, via the same rater callers use."""
        from combat.difficulty import DynamicDifficulty, EncounterProfile

        engine = DynamicDifficulty()
        crs = [m["cr"] for m in monsters for _ in range(m.get("count", 1))]
        profile = EncounterProfile([m["name"] for m in monsters], crs)
        party = engine.get_party_composition(party_size, float(party_level))
        return engine.calculate_difficulty(profile, party).value

    def _generate_name(self, environment: str) -> str:
        """Generate an encounter name."""
        prefixes = ["The", "Ambush at", "Battle in the", "Siege of", "Strange happenings"]
        return f"{random.choice(prefixes)} {environment}"

    def _generate_description(self, environment: str, monsters: List[Dict]) -> str:
        """Generate encounter description."""
        monster_types = ", ".join(m["name"] for m in monsters)
        descriptions = [
            f"A group of {monster_types} guard {environment}.",
            f"You stumble upon {monster_types} camping in {environment}.",
            f"A desperate battle rages as {monster_types} attack from {environment}.",
            f"The sound of combat draws you to {environment}, where {monster_types} are fighting.",
        ]
        return random.choice(descriptions)

    def _generate_hooks(self, environment: str, monster_count: int) -> List[str]:
        """Generate roleplay hooks for the encounter."""
        hooks = [
            "What brought these creatures here?",
            "Can any of them be reasoned with?",
            f"Is {environment} their home or a temporary camp?",
            "Are there clues about their leader or organization?",
            "Could they be escaped prisoners or enslaved creatures?",
        ]
        return random.sample(hooks, min(3, len(hooks)))
