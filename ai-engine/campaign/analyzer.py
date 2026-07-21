"""Campaign analysis and module synergy mapping."""

import json
from dataclasses import dataclass
from typing import Any, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class NarrativeElement:
    """Represents a narrative element in the campaign."""
    type: str  # scene, encounter, npc, transition, lore, etc.
    name: str
    description: str
    immersion_opportunities: list[str]
    required_player_engagement: str  # "combat", "dialogue", "exploration", "puzzle"
    drama_level: int  # 1-10 intensity


@dataclass
class ModuleCapability:
    """Represents what a module can do."""
    module_id: str
    name: str
    enabled: bool
    capabilities: list[str]
    narrative_hooks: list[str]  # How it can enhance storytelling


class CampaignAnalyzer:
    """Analyzes campaign structure and narrative elements."""

    def __init__(self):
        self.logger = logger

    async def analyze_campaign(self, campaign_data: dict) -> dict:
        """Analyze campaign for narrative and immersion opportunities."""
        self.logger.info("Analyzing campaign structure and narrative elements...")

        analysis = {
            "scenes": await self._analyze_scenes(campaign_data.get("scenes", [])),
            "encounters": await self._analyze_encounters(campaign_data.get("encounters", [])),
            "npcs": await self._analyze_npcs(campaign_data.get("npcs", [])),
            "narrative_arcs": await self._identify_narrative_arcs(campaign_data),
            "pacing": await self._analyze_pacing(campaign_data.get("scenes", [])),
            "decision_points": await self._find_decision_points(campaign_data),
            "immersion_gaps": await self._identify_immersion_gaps(campaign_data),
        }

        self.logger.info(
            f"Campaign analysis complete: {len(analysis['scenes'])} scenes, "
            f"{len(analysis['encounters'])} encounters, {len(analysis['npcs'])} NPCs"
        )
        return analysis

    async def _analyze_scenes(self, scenes: list) -> list[NarrativeElement]:
        """Analyze individual scenes for immersion opportunities."""
        narrative_elements = []

        for scene in scenes:
            element = NarrativeElement(
                type="scene",
                name=scene.get("name", "Unknown Scene"),
                description=scene.get("description", ""),
                immersion_opportunities=self._generate_scene_opportunities(scene),
                required_player_engagement=self._determine_engagement_type(scene),
                drama_level=self._rate_drama(scene),
            )
            narrative_elements.append(element)

        return narrative_elements

    async def _analyze_encounters(self, encounters: list) -> list[NarrativeElement]:
        """Analyze encounters for narrative tension and opportunities."""
        narrative_elements = []

        for encounter in encounters:
            element = NarrativeElement(
                type="encounter",
                name=encounter.get("name", "Unnamed Encounter"),
                description=encounter.get("description", ""),
                immersion_opportunities=self._generate_encounter_opportunities(encounter),
                required_player_engagement="combat",
                drama_level=self._rate_encounter_intensity(encounter),
            )
            narrative_elements.append(element)

        return narrative_elements

    async def _analyze_npcs(self, npcs: list) -> list[NarrativeElement]:
        """Analyze NPCs for character development and dialogue opportunities."""
        narrative_elements = []

        for npc in npcs:
            element = NarrativeElement(
                type="npc",
                name=npc.get("name", "Unknown NPC"),
                description=npc.get("description", ""),
                immersion_opportunities=self._generate_npc_opportunities(npc),
                required_player_engagement="dialogue",
                drama_level=self._rate_npc_importance(npc),
            )
            narrative_elements.append(element)

        return narrative_elements

    async def _identify_narrative_arcs(self, campaign_data: dict) -> list[dict]:
        """Identify main narrative arcs and story progression."""
        arcs = []

        # Look for quest logs or story milestones. The canonical key is
        # "quest_logs" (generated + imported campaigns emit that); "quests" is
        # only a legacy alias — read both so arcs aren't silently empty.
        quests = campaign_data.get("quest_logs") or campaign_data.get("quests") or []
        for quest in quests:
            arcs.append({
                "title": quest.get("title", ""),
                "progression": quest.get("stages", []),
                "key_moments": self._extract_key_moments(quest),
                "player_agency": quest.get("choices", []),
            })

        return arcs

    async def _analyze_pacing(self, scenes: list) -> dict:
        """Analyze campaign pacing and intensity distribution."""
        if not scenes:
            return {"average_intensity": 0, "pacing_variance": 0}

        intensities = [self._rate_drama(scene) for scene in scenes]
        avg_intensity = sum(intensities) / len(intensities) if intensities else 0

        return {
            "average_intensity": avg_intensity,
            "peak_intensity": max(intensities) if intensities else 0,
            "scene_count": len(scenes),
            "pacing_variance": self._calculate_variance(intensities),
        }

    async def _find_decision_points(self, campaign_data: dict) -> list[dict]:
        """Identify where players make meaningful choices."""
        decision_points = []

        for scene in campaign_data.get("scenes", []):
            if "choice" in scene.get("description", "").lower():
                decision_points.append({
                    "scene": scene.get("name"),
                    "type": "branching_path",
                    "options": scene.get("choices", []),
                })

        return decision_points

    async def _identify_immersion_gaps(self, campaign_data: dict) -> list[str]:
        """Identify areas where immersion could be enhanced."""
        gaps = []

        scenes = campaign_data.get("scenes", [])
        for scene in scenes:
            setup = scene.get("scene_setup", {})

            if not setup.get("lights"):
                gaps.append(f"Scene '{scene.get('name')}' lacks lighting setup")

            if not setup.get("sounds"):
                gaps.append(f"Scene '{scene.get('name')}' lacks ambient sounds")

            if not scene.get("music"):
                gaps.append(f"Scene '{scene.get('name')}' has no music/theme")

        return gaps

    def _generate_scene_opportunities(self, scene: dict) -> list[str]:
        """Generate immersion enhancement opportunities for a scene."""
        opportunities = []
        setup = scene.get("scene_setup", {})

        if setup.get("walls"):
            opportunities.append("Use wall animations for atmospheric effects")

        if not setup.get("lights"):
            opportunities.append("Add dynamic lighting to set mood")

        if not setup.get("sounds"):
            opportunities.append("Add ambient soundscape")

        # More opportunities based on scene type
        description = scene.get("description", "").lower()
        if "combat" in description or "fight" in description:
            opportunities.append("Use combat effects modules for dramatic encounters")

        if "dialogue" in description or "meeting" in description:
            opportunities.append("Use notification system for character thoughts")

        if "exploration" in description:
            opportunities.append("Add discovery notifications and environmental feedback")

        return opportunities

    def _generate_encounter_opportunities(self, encounter: dict) -> list[str]:
        """Generate enhancement opportunities for an encounter."""
        opportunities = [
            "Sync encounter progression with module automation",
            "Use dice effects for dramatic rolls",
            "Add initiative order enhancements",
            "Create dynamic difficulty scaling",
            "Track combat state for narrative consequences",
        ]
        return opportunities

    def _generate_npc_opportunities(self, npc: dict) -> list[str]:
        """Generate dialogue and interaction opportunities for NPCs."""
        opportunities = [
            "Create dialogue branching with player choices",
            "Add character development through repeated interactions",
            "Generate relationship tracking",
            "Create quest hooks from NPC motivations",
        ]
        return opportunities

    def _determine_engagement_type(self, scene: dict) -> str:
        """Determine primary player engagement type for a scene."""
        description = scene.get("description", "").lower()

        if any(word in description for word in ["combat", "fight", "battle", "ambush"]):
            return "combat"
        elif any(word in description for word in ["talk", "dialogue", "negotiate", "meet"]):
            return "dialogue"
        elif any(word in description for word in ["explore", "search", "discover", "investigate"]):
            return "exploration"
        elif any(word in description for word in ["puzzle", "riddle", "solve", "trap"]):
            return "puzzle"
        else:
            return "mixed"

    def _rate_drama(self, scene: dict) -> int:
        """Rate dramatic intensity of a scene (1-10)."""
        drama = 5  # base level

        description = scene.get("description", "").lower()

        intensity_keywords = {
            "betrayal": 10,
            "death": 9,
            "revelation": 8,
            "confrontation": 7,
            "danger": 6,
            "discovery": 6,
            "emotional": 7,
        }

        for keyword, intensity in intensity_keywords.items():
            if keyword in description:
                drama = max(drama, intensity)

        return min(drama, 10)

    def _rate_encounter_intensity(self, encounter: dict) -> int:
        """Rate combat intensity of an encounter (1-10)."""
        base = 5
        token_count = encounter.get("tokens_placed", 0)
        intensity = min(5 + (token_count // 2), 10)
        return intensity

    def _rate_npc_importance(self, npc: dict) -> int:
        """Rate importance/development potential of an NPC (1-10)."""
        importance = 5

        if npc.get("is_questgiver"):
            importance = 9
        elif npc.get("is_companion"):
            importance = 8
        elif npc.get("is_villain"):
            importance = 9
        elif npc.get("is_ally"):
            importance = 7

        return min(importance, 10)

    def _extract_key_moments(self, quest: dict) -> list[str]:
        """Extract key story moments from a quest."""
        moments = []

        stages = quest.get("stages", [])
        for stage in stages:
            if isinstance(stage, dict) and stage.get("description"):
                moments.append(stage.get("description"))

        return moments

    def _calculate_variance(self, values: list[float]) -> float:
        """Calculate variance of a list of values."""
        if not values:
            return 0

        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5  # return standard deviation
