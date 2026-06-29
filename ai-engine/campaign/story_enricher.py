"""Generate module-specific story enhancements for campaigns."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class StoryEnricher:
    """Generates AI-powered story enhancements based on available modules."""

    def __init__(self, llm_client=None):
        self.logger = logger
        self.llm_client = llm_client

    async def generate_enhancements(
        self, campaign_analysis: dict, module_synergies: dict, llm_manager=None
    ) -> dict:
        """Generate specific story enhancements based on modules and narrative."""
        self.logger.info("Generating module-specific story enhancements...")

        enhancements = {
            "scene_hooks": await self._generate_scene_hooks(
                campaign_analysis.get("scenes", []),
                module_synergies.get("scene_enhancements", []),
                llm_manager,
            ),
            "encounter_moments": await self._generate_encounter_moments(
                campaign_analysis.get("encounters", []),
                module_synergies.get("encounter_enhancements", []),
                llm_manager,
            ),
            "npc_interactions": await self._generate_npc_interactions(
                campaign_analysis.get("npcs", []),
                module_synergies.get("npc_enhancements", []),
                llm_manager,
            ),
            "dramatic_moments": await self._generate_dramatic_moments(
                campaign_analysis, llm_manager
            ),
            "world_dynamics": await self._generate_world_dynamics(
                campaign_analysis, module_synergies, llm_manager
            ),
        }

        return enhancements

    async def _generate_scene_hooks(self, scenes: list, synergies: list, llm_manager) -> list[dict]:
        """Generate immersion hooks for each scene."""
        hooks = []

        for scene in scenes:
            # Find synergies for this scene
            scene_synergies = next(
                (s for s in synergies if s.get("scene") == scene.get("name")), None
            )

            if scene_synergies and llm_manager:
                # Use LLM to generate immersive descriptions
                prompt = f"""
                Create immersive scene hooks for a D&D scene using these modules:
                {[s['module'] for s in scene_synergies['synergies']]}

                Scene: {scene['name']}
                Description: {scene['description']}
                Drama Level: {scene['drama_level']}/10

                Generate 3 atmospheric hooks that leverage the available modules to create immersion.
                Format as JSON array of objects with 'hook' and 'module_used' fields.
                """

                try:
                    response = await llm_manager.generate_with_context(
                        prompt, context={"scene": scene.get("name")}
                    )
                    # Parse response - would need actual LLM implementation
                    hooks.append({
                        "scene": scene.get("name"),
                        "hooks": [{"hook": response, "module_used": "multiple"}],
                    })
                except Exception as e:
                    self.logger.warning(f"Failed to generate hooks for {scene.get('name')}: {e}")

        return hooks

    async def _generate_encounter_moments(self, encounters: list, synergies: list, llm_manager) -> list[dict]:
        """Generate dramatic moments for encounters using available modules."""
        moments = []

        for encounter in encounters:
            encounter_synergies = next(
                (s for s in synergies if s.get("encounter") == encounter.get("name")), None
            )

            if encounter_synergies:
                moment = {
                    "encounter": encounter.get("name"),
                    "drama_level": encounter.get("drama_level", 5),
                    "module_enhancements": encounter_synergies.get("synergies", []),
                    "dramatic_beats": [
                        "Opening tension setup using module effects",
                        "Critical roll moment with dramatic visualization",
                        "Tactical turning point with narrative weight",
                        "Climactic resolution synchronized with effects",
                    ],
                }
                moments.append(moment)

        return moments

    async def _generate_npc_interactions(self, npcs: list, synergies: list, llm_manager) -> list[dict]:
        """Generate rich NPC interaction opportunities."""
        interactions = []

        for npc in npcs:
            npc_synergies = next(
                (s for s in synergies if s.get("npc") == npc.get("name")), None
            )

            if npc_synergies:
                interaction = {
                    "npc": npc.get("name"),
                    "description": npc.get("description", ""),
                    "interaction_types": [
                        "First Meeting - Create memorable introduction",
                        "Relationship Building - Track connection development",
                        "Conflict Resolution - Navigate disagreement with depth",
                        "Betrayal Moment - Dramatic revelation with narrative weight",
                    ],
                    "module_features": [s.get("enhancement") for s in npc_synergies.get("synergies", [])],
                }
                interactions.append(interaction)

        return interactions

    async def _generate_dramatic_moments(self, campaign_analysis: dict, llm_manager) -> list[dict]:
        """Generate key dramatic moments using module capabilities."""
        moments = []

        decision_points = campaign_analysis.get("decision_points", [])
        for decision in decision_points:
            moments.append({
                "type": "branching_choice",
                "scene": decision.get("scene"),
                "choice_count": len(decision.get("options", [])),
                "narrative_weight": "high",
                "module_support": "Use notification system to show consequences preview",
            })

        # Add pacing-based dramatic moments
        pacing = campaign_analysis.get("pacing", {})
        if pacing.get("peak_intensity", 0) > 7:
            moments.append({
                "type": "climactic_confrontation",
                "intensity_level": pacing.get("peak_intensity"),
                "narrative_weight": "critical",
                "module_support": "Synchronize all available effects for maximum impact",
            })

        return moments

    async def _generate_world_dynamics(self, campaign_analysis: dict, module_synergies: dict, llm_manager) -> dict:
        """Generate dynamic world evolution opportunities."""
        dynamics = {
            "time_progression": {
                "enabled": "simple-calendar" in str(module_synergies),
                "opportunities": [
                    "Create seasonal narrative shifts",
                    "Track NPC schedules and availability",
                    "Generate time-pressure story hooks",
                    "Create sense of world aging and change",
                ],
            },
            "consequence_tracking": {
                "enabled": "betterrolls5e" in str(module_synergies),
                "opportunities": [
                    "Link critical rolls to lasting story impact",
                    "Create NPC reactions to character actions",
                    "Build reputation system through successes/failures",
                ],
            },
            "atmospheric_evolution": {
                "enabled": "animated-spell-effects-cartoon" in str(module_synergies),
                "opportunities": [
                    "Create magical environmental changes",
                    "Build tension through visual effects escalation",
                    "Synchronize battle effects with narrative pacing",
                ],
            },
            "player_agency": {
                "enabled": True,
                "opportunities": [
                    f"Highlight {len(campaign_analysis.get('decision_points', []))} key decision points",
                    "Show branching narrative paths",
                    "Create meaningful choice consequences",
                ],
            },
        }

        return dynamics
