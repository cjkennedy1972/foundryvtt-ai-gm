"""Orchestrates campaign analysis and module-based optimization."""

import logging
from .analyzer import CampaignAnalyzer
from .module_discovery import ModuleDiscovery, ModuleSynergyMapper
from .story_enricher import StoryEnricher

logger = logging.getLogger(__name__)


class CampaignOptimizer:
    """Analyzes campaigns and generates module-specific enhancements for maximum immersion."""

    def __init__(self, llm_manager=None):
        self.logger = logger
        self.llm_manager = llm_manager
        self.analyzer = CampaignAnalyzer()
        self.module_discovery = ModuleDiscovery()
        self.synergy_mapper = ModuleSynergyMapper()
        self.story_enricher = StoryEnricher(llm_client=llm_manager)

    async def optimize_campaign(self, campaign_data: dict, foundry_client) -> dict:
        """Run full campaign optimization pipeline."""
        self.logger.info("Starting campaign optimization...")

        try:
            # Phase 1: Analyze campaign
            self.logger.info("Phase 1: Analyzing campaign structure...")
            campaign_analysis = await self.analyzer.analyze_campaign(campaign_data)

            # Phase 2: Discover modules
            self.logger.info("Phase 2: Discovering Foundry modules...")
            module_discovery = await self.module_discovery.discover_modules(foundry_client)

            if "error" in module_discovery:
                self.logger.warning(f"Module discovery failed: {module_discovery['error']}")
                module_discovery = {"modules": [], "total_modules": 0, "enabled_modules": 0}

            # Phase 3: Map synergies
            self.logger.info("Phase 3: Mapping module synergies...")
            module_synergies = await self.synergy_mapper.map_synergies(
                campaign_analysis, module_discovery.get("modules", [])
            )

            # Phase 4: Generate enhancements
            self.logger.info("Phase 4: Generating story enhancements...")
            enhancements = await self.story_enricher.generate_enhancements(
                campaign_analysis, module_synergies, self.llm_manager
            )

            # Compile results
            optimization_result = {
                "status": "complete",
                "campaign_name": campaign_data.get("name", "Unknown"),
                "analysis": {
                    "scene_count": len(campaign_analysis.get("scenes", [])),
                    "encounter_count": len(campaign_analysis.get("encounters", [])),
                    "npc_count": len(campaign_analysis.get("npcs", [])),
                    "narrative_arcs": len(campaign_analysis.get("narrative_arcs", [])),
                    "drama_analysis": campaign_analysis.get("pacing", {}),
                    "immersion_gaps_identified": len(campaign_analysis.get("immersion_gaps", [])),
                },
                "modules": {
                    "total_installed": module_discovery.get("total_modules", 0),
                    "enabled": module_discovery.get("enabled_modules", 0),
                    "modules_list": [
                        {
                            "id": m.id,
                            "name": m.name,
                            "enabled": m.enabled,
                            "capabilities": m.capabilities,
                            "narrative_uses": m.narrative_use_cases,
                        }
                        for m in module_discovery.get("modules", [])
                    ],
                },
                "synergies": {
                    "scene_synergies": len(module_synergies.get("scene_enhancements", [])),
                    "encounter_synergies": len(module_synergies.get("encounter_enhancements", [])),
                    "npc_synergies": len(module_synergies.get("npc_enhancements", [])),
                    "immersion_gap_fills": len(module_synergies.get("immersion_gap_fills", [])),
                    "details": module_synergies,
                },
                "enhancements": enhancements,
                "recommendations": self._generate_recommendations(
                    campaign_analysis, module_discovery.get("modules", []), module_synergies
                ),
            }

            self.logger.info("Campaign optimization complete")
            return optimization_result

        except Exception as e:
            self.logger.error(f"Campaign optimization failed: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    def _generate_recommendations(self, analysis: dict, modules: list, synergies: dict) -> list[dict]:
        """Generate actionable recommendations for the GM."""
        recommendations = []

        # Check for missing immersion elements
        immersion_gaps = analysis.get("immersion_gaps", [])
        if immersion_gaps:
            recommendations.append({
                "priority": "high",
                "category": "Immersion Gaps",
                "count": len(immersion_gaps),
                "action": "Consider installing modules to address these gaps",
                "details": immersion_gaps[:3],  # Top 3 gaps
            })

        # Check for high-drama scenes without module support
        scenes = analysis.get("scenes", [])
        high_drama_unsupported = [
            s for s in scenes if s.get("drama_level", 0) > 7
            and not any(syn.get("scene") == s.get("name") for syn in synergies.get("scene_enhancements", []))
        ]

        if high_drama_unsupported:
            recommendations.append({
                "priority": "medium",
                "category": "Drama Enhancement",
                "count": len(high_drama_unsupported),
                "action": "These high-drama scenes could benefit from combat/effect modules",
                "details": [s.get("name") for s in high_drama_unsupported[:3]],
            })

        # Check for extensive NPC interactions
        npcs = analysis.get("npcs", [])
        if len(npcs) > 5:
            recommendations.append({
                "priority": "medium",
                "category": "NPC Management",
                "count": len(npcs),
                "action": "Consider journal and notification modules for tracking NPC relationships",
                "details": ["Install journal-entrypage-tabs for character trees"],
            })

        # Check for decision points
        decisions = analysis.get("decision_points", [])
        if len(decisions) > 3:
            recommendations.append({
                "priority": "low",
                "category": "Player Agency",
                "count": len(decisions),
                "action": "Create branching story content to maximize narrative impact of choices",
                "details": [f"{len(decisions)} key decision points identified"],
            })

        return recommendations
