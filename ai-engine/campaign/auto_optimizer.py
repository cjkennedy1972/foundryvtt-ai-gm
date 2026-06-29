"""Auto-optimizer that enriches newly created campaign elements."""

import logging
from typing import Optional
from .campaign_optimizer import CampaignOptimizer

logger = logging.getLogger(__name__)


class AutoOptimizer:
    """Automatically enriches newly created scenes, encounters, and quests with module synergies."""

    def __init__(self, llm_manager=None, foundry_client=None):
        self.logger = logger
        self.llm_manager = llm_manager
        self.foundry_client = foundry_client
        self.optimizer = CampaignOptimizer(llm_manager=llm_manager)

    async def optimize_new_scene(
        self, scene_data: dict, campaign_data: dict
    ) -> dict:
        """Optimize a newly created scene with module enhancements."""
        self.logger.info(f"Auto-optimizing new scene: {scene_data.get('name')}")

        try:
            # Create a minimal campaign subset for analysis
            minimal_campaign = {
                "name": campaign_data.get("name"),
                "scenes": [scene_data],
                "encounters": [],
                "npcs": [],
            }

            # Run optimization on just this scene
            result = await self.optimizer.optimize_campaign(
                minimal_campaign, self.foundry_client
            )

            if result.get("status") == "complete":
                enhancements = {
                    "scene_name": scene_data.get("name"),
                    "modules": result.get("modules", {}),
                    "synergies": result.get("synergies", {}).get("scene_enhancements", []),
                    "recommendations": result.get("recommendations", []),
                }

                self.logger.info(
                    f"Scene '{scene_data.get('name')}' optimized with "
                    f"{len(enhancements.get('synergies', []))} module synergies"
                )

                return enhancements
            else:
                self.logger.warning(f"Scene optimization failed: {result.get('error')}")
                return {"error": result.get("error")}

        except Exception as e:
            self.logger.error(f"Scene auto-optimization error: {e}", exc_info=True)
            return {"error": str(e)}

    async def optimize_new_encounter(
        self, encounter_data: dict, campaign_data: dict
    ) -> dict:
        """Optimize a newly created encounter with module enhancements."""
        self.logger.info(f"Auto-optimizing new encounter: {encounter_data.get('name')}")

        try:
            # Create a minimal campaign subset for analysis
            minimal_campaign = {
                "name": campaign_data.get("name"),
                "scenes": [],
                "encounters": [encounter_data],
                "npcs": [],
            }

            # Run optimization on just this encounter
            result = await self.optimizer.optimize_campaign(
                minimal_campaign, self.foundry_client
            )

            if result.get("status") == "complete":
                enhancements = {
                    "encounter_name": encounter_data.get("name"),
                    "modules": result.get("modules", {}),
                    "synergies": result.get("synergies", {}).get("encounter_enhancements", []),
                    "recommendations": result.get("recommendations", []),
                }

                self.logger.info(
                    f"Encounter '{encounter_data.get('name')}' optimized with "
                    f"{len(enhancements.get('synergies', []))} module synergies"
                )

                return enhancements
            else:
                self.logger.warning(f"Encounter optimization failed: {result.get('error')}")
                return {"error": result.get("error")}

        except Exception as e:
            self.logger.error(f"Encounter auto-optimization error: {e}", exc_info=True)
            return {"error": str(e)}

    async def optimize_new_quest(self, quest_data: dict, campaign_data: dict) -> dict:
        """Optimize a newly created quest with narrative enhancements."""
        self.logger.info(f"Auto-optimizing new quest: {quest_data.get('title')}")

        try:
            # Create a minimal campaign subset for analysis
            minimal_campaign = {
                "name": campaign_data.get("name"),
                "scenes": [],
                "encounters": [],
                "npcs": [],
                "quests": [quest_data],
            }

            # Run optimization on just this quest
            result = await self.optimizer.optimize_campaign(
                minimal_campaign, self.foundry_client
            )

            if result.get("status") == "complete":
                enhancements = {
                    "quest_title": quest_data.get("title"),
                    "modules": result.get("modules", {}),
                    "narrative_enhancements": result.get("enhancements", {}),
                    "recommendations": result.get("recommendations", []),
                }

                self.logger.info(
                    f"Quest '{quest_data.get('title')}' optimized with narrative enhancements"
                )

                return enhancements
            else:
                self.logger.warning(f"Quest optimization failed: {result.get('error')}")
                return {"error": result.get("error")}

        except Exception as e:
            self.logger.error(f"Quest auto-optimization error: {e}", exc_info=True)
            return {"error": str(e)}

    async def optimize_element_batch(
        self, elements: list[dict], element_type: str, campaign_data: dict
    ) -> list[dict]:
        """Batch optimize multiple new elements (scenes, encounters, or quests)."""
        self.logger.info(f"Batch optimizing {len(elements)} new {element_type}...")

        results = []
        for element in elements:
            if element_type == "scene":
                result = await self.optimize_new_scene(element, campaign_data)
            elif element_type == "encounter":
                result = await self.optimize_new_encounter(element, campaign_data)
            elif element_type == "quest":
                result = await self.optimize_new_quest(element, campaign_data)
            else:
                result = {"error": f"Unknown element type: {element_type}"}

            results.append(result)

        return results
