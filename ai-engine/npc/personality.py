"""NPC personality parsing and management."""

import logging
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


# Personality trait keywords organized by category
PERSONALITY_KEYWORDS = {
    "temperament": {
        "aggressive": ["aggressive", "violent", "hostile", "combative", "belligerent"],
        "calm": ["calm", "peaceful", "serene", "tranquil", "composed"],
        "excitable": ["excited", "excitable", "energetic", "hyperactive", "manic"],
        "melancholic": ["sad", "melancholy", "gloomy", "depressed", "sorrowful"],
        "cheerful": ["cheerful", "happy", "joyful", "bubbly", "upbeat"],
        "stoic": ["stoic", "impassive", "unemotional", "unflappable"],
    },
    "intellect": {
        "intelligent": ["intelligent", "clever", "smart", "brilliant", "genius"],
        "foolish": ["foolish", "stupid", "dumb", "idiotic", "simple-minded"],
        "scholarly": ["scholarly", "learned", "educated", "bookish", "academic"],
        "naive": ["naive", "gullible", "innocent", "simple", "trusting"],
        "cunning": ["cunning", "clever", "sly", "devious", "shrewd"],
    },
    "morality": {
        "good": ["good", "righteous", "virtuous", "moral", "honorable"],
        "evil": ["evil", "wicked", "malicious", "sinister", "vile"],
        "neutral": ["neutral", "amoral", "pragmatic", "utilitarian"],
        "chaotic": ["chaotic", "unpredictable", "reckless", "impulsive"],
        "lawful": ["lawful", "orderly", "rule-abiding", "strict", "disciplined"],
    },
    "sociability": {
        "friendly": ["friendly", "warm", "hospitable", "sociable", "gregarious"],
        "hostile": ["hostile", "unfriendly", "cold", "aloof", "distant"],
        "talkative": ["talkative", "chatty", "verbose", "loquacious"],
        "quiet": ["quiet", "reserved", "taciturn", "silent", "withdrawn"],
        "charming": ["charming", "charismatic", "likable", "engaging", "personable"],
    },
    "courage": {
        "brave": ["brave", "courageous", "fearless", "bold", "heroic"],
        "cowardly": ["cowardly", "timid", "fearful", "cautious", "craven"],
        "reckless": ["reckless", "foolhardy", "daring", "audacious"],
    },
}


@dataclass
class NPCPersonality:
    """Represents the personality profile of an NPC."""

    npc_id: str
    npc_name: str
    description: str
    traits: Dict[str, List[str]] = field(default_factory=dict)
    strengths: List[str] = field(default_factory=list)
    flaws: List[str] = field(default_factory=list)
    motivations: List[str] = field(default_factory=list)
    mannerisms: List[str] = field(default_factory=list)
    speech_pattern: Optional[str] = None
    relationships: Dict[str, str] = field(default_factory=dict)
    consistency_score: float = 1.0

    def to_prompt_context(self) -> str:
        """Generate a concise prompt context for this NPC."""
        lines = [f"**{self.npc_name}**"]

        if self.traits:
            trait_list = "; ".join(
                f"{cat}: {', '.join(traits)}"
                for cat, traits in self.traits.items()
                if traits
            )
            if trait_list:
                lines.append(f"Personality: {trait_list}")

        if self.strengths:
            lines.append(f"Strengths: {', '.join(self.strengths)}")

        if self.flaws:
            lines.append(f"Flaws: {', '.join(self.flaws)}")

        if self.motivations:
            lines.append(f"Motivations: {', '.join(self.motivations)}")

        if self.mannerisms:
            lines.append(f"Mannerisms: {', '.join(self.mannerisms)}")

        if self.speech_pattern:
            lines.append(f"Speech: {self.speech_pattern}")

        if self.relationships:
            rel_list = "; ".join(f"{name}: {rel}" for name, rel in self.relationships.items())
            lines.append(f"Relationships: {rel_list}")

        return "\n".join(lines)


class PersonalityEngine:
    """Parse and manage NPC personalities from descriptions."""

    def __init__(self):
        self.keywords = PERSONALITY_KEYWORDS
        self.parsed_npcs: Dict[str, NPCPersonality] = {}

    def parse_npc_description(
        self, npc_id: str, npc_name: str, description: str
    ) -> NPCPersonality:
        """Parse an NPC description and extract personality traits."""
        personality = NPCPersonality(
            npc_id=npc_id,
            npc_name=npc_name,
            description=description,
        )

        # Extract trait keywords from description
        desc_lower = description.lower()
        personality.traits = self._extract_traits(desc_lower)

        # Extract other personality elements
        personality.strengths = self._extract_section(desc_lower, "strength", "strong in", "good at")
        personality.flaws = self._extract_section(desc_lower, "flaw", "weakness", "afraid of")
        personality.motivations = self._extract_section(
            desc_lower, "motivation", "wants", "seeks", "desires"
        )
        personality.mannerisms = self._extract_section(desc_lower, "manner", "habit", "quirk")
        personality.speech_pattern = self._extract_speech_pattern(desc_lower)

        # Store parsed personality
        self.parsed_npcs[npc_id] = personality
        logger.info(f"Parsed personality for {npc_name}: {list(personality.traits.keys())}")

        return personality

    def _extract_traits(self, description: str) -> Dict[str, List[str]]:
        """Extract personality traits from description text."""
        traits = {}

        for category, trait_map in self.keywords.items():
            matching_traits = []
            for trait_name, keywords in trait_map.items():
                if any(kw in description for kw in keywords):
                    matching_traits.append(trait_name)
            if matching_traits:
                traits[category] = matching_traits

        return traits

    def _extract_section(
        self, description: str, *keywords: str
    ) -> List[str]:
        """Extract specific sections from description based on keywords."""
        results = []

        # Look for patterns like "keyword: value" or "keyword of value"
        for keyword in keywords:
            pattern = rf"{keyword}(?:\s+of|\s*:)?\s+([^.,;]+)"
            matches = re.findall(pattern, description, re.IGNORECASE)
            results.extend([m.strip() for m in matches if m.strip()])

        return list(set(results))  # Remove duplicates

    def _extract_speech_pattern(self, description: str) -> Optional[str]:
        """Extract speech patterns or accent information."""
        speech_keywords = [
            "speaks",
            "accent",
            "dialect",
            "voice",
            "tone",
            "manner of speaking",
            "says things like",
            "often says",
        ]

        for keyword in speech_keywords:
            pattern = rf"{keyword}(?:\s+of|\s*:)?\s+([^.,;]+)"
            matches = re.findall(pattern, description, re.IGNORECASE)
            if matches:
                return matches[0].strip()

        return None

    def get_npc_personality(self, npc_id: str) -> Optional[NPCPersonality]:
        """Retrieve a parsed NPC personality."""
        return self.parsed_npcs.get(npc_id)

    def get_npc_context(self, npc_id: str) -> str:
        """Get formatted context for an NPC personality."""
        personality = self.get_npc_personality(npc_id)
        if not personality:
            return ""
        return personality.to_prompt_context()

    def check_consistency(self, npc_id: str, recent_dialogue: List[str]) -> Tuple[bool, str]:
        """Check if recent NPC dialogue is consistent with personality.

        Returns (is_consistent, explanation).
        """
        personality = self.get_npc_personality(npc_id)
        if not personality:
            return True, "No personality profile loaded"

        # Simple consistency check based on trait keywords
        dialogue_text = " ".join(recent_dialogue).lower()
        found_traits = self._extract_traits(dialogue_text)

        consistency_score = 0.0
        matching_traits = 0

        for category, traits in personality.traits.items():
            category_found = found_traits.get(category, [])
            if category_found:
                for trait in traits:
                    if trait in category_found:
                        matching_traits += 1

        if personality.traits:
            total_traits = sum(len(traits) for traits in personality.traits.values())
            consistency_score = min(1.0, matching_traits / max(1, total_traits))

        personality.consistency_score = consistency_score
        is_consistent = consistency_score >= 0.5

        explanation = f"Consistency: {consistency_score:.0%} ({matching_traits}/{sum(len(t) for t in personality.traits.values())} traits matched)"

        return is_consistent, explanation
