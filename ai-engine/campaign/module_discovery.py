"""Discover and map Foundry modules for campaign enhancement."""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ModuleInfo:
    """Information about an installed Foundry module."""
    id: str
    name: str
    version: str
    enabled: bool
    description: str
    capabilities: list[str]
    narrative_use_cases: list[str]


class ModuleDiscovery:
    """Discovers installed modules in Foundry and their capabilities."""

    # Known module IDs and their capabilities
    MODULE_CAPABILITIES = {
        "animated-spell-effects-cartoon": {
            "name": "Animated Spell Effects - Cartoon",
            "capabilities": ["spell_effects", "combat_animation", "visual_feedback"],
            "narrative_uses": [
                "Enhance magical encounters with visual effects",
                "Create dramatic spell descriptions synchronized with animations",
                "Emphasize magical abilities in combat storytelling",
            ],
        },
        "betterrolls5e": {
            "name": "Better Rolls 5e",
            "capabilities": ["enhanced_rolls", "roll_tracking", "automatic_bonuses"],
            "narrative_uses": [
                "Build tension with enhanced roll displays",
                "Track critical successes/failures for story impact",
                "Create dramatic moment notifications on nat 20s",
            ],
        },
        "simple-calendar": {
            "name": "Simple Calendar",
            "capabilities": ["time_tracking", "seasonal_events", "calendar_integration"],
            "narrative_uses": [
                "Create time-based story hooks and deadlines",
                "Track NPC schedules and availability",
                "Generate seasonal narrative elements",
                "Create sense of world progression",
            ],
        },
        "notification": {
            "name": "Notification Tooltips",
            "capabilities": ["notifications", "message_display", "ui_feedback"],
            "narrative_uses": [
                "Display character thoughts and internal monologues",
                "Hint at hidden plot elements",
                "Track player character emotional states",
                "Deliver environmental awareness messages",
            ],
        },
        "dice-so-nice": {
            "name": "Dice So Nice!",
            "capabilities": ["dice_effects", "visual_randomization", "dramatic_display"],
            "narrative_uses": [
                "Create dramatic dice moment storytelling",
                "Emphasize important rolls with customized effects",
                "Build suspense with dice animation pacing",
                "Celebrate critical successes with visual fanfare",
            ],
        },
        "combat-carousel": {
            "name": "Combat Carousel",
            "capabilities": ["turn_order_display", "combat_ui", "initiative_tracking"],
            "narrative_uses": [
                "Enhance tactical awareness for complex encounters",
                "Track combatant status for narrative awareness",
                "Build tactical storytelling commentary",
            ],
        },
        "socketlib": {
            "name": "Socket Lib",
            "capabilities": ["socket_communication", "real_time_sync", "player_sync"],
            "narrative_uses": [
                "Synchronize narrative events across players",
                "Create real-time atmospheric changes",
                "Coordinate multi-player story moments",
            ],
        },
        "journal-entrypage-tabs": {
            "name": "Journal EntryPage Tabs",
            "capabilities": ["journal_organization", "content_tabs", "story_structure"],
            "narrative_uses": [
                "Create branching lore trees",
                "Organize character backstories hierarchically",
                "Build interconnected quest narratives",
                "Layer narrative complexity",
            ],
        },
        "pf2e-toolbelt": {
            "name": "PF2e Toolbelt",
            "capabilities": ["automation", "rule_enforcement", "mechanical_shortcuts"],
            "narrative_uses": [
                "Automate rule-heavy moments to focus on narrative",
                "Track mechanical story consequences automatically",
            ],
        },
        "midi-qol": {
            "name": "MidiQOL",
            "capabilities": ["automation", "attack_rolls", "damage_application"],
            "narrative_uses": [
                "Create cinematic combat with automated flow",
                "Focus GM attention on storytelling over mechanics",
                "Synchronize damage with narrative consequences",
            ],
        },
    }

    def __init__(self):
        self.logger = logger

    async def discover_modules(self, foundry_client) -> dict:
        """Discover all installed and enabled modules in Foundry."""
        self.logger.info("Discovering installed modules in Foundry...")

        try:
            # Get module list from Foundry
            modules = await self._fetch_modules(foundry_client)

            # Enhance with capability information
            enhanced_modules = []
            for module_id, module_data in modules.items():
                enhanced = await self._enhance_module_info(module_id, module_data)
                if enhanced:
                    enhanced_modules.append(enhanced)

            self.logger.info(f"Discovered {len(enhanced_modules)} modules")
            return {
                "total_modules": len(enhanced_modules),
                "enabled_modules": len([m for m in enhanced_modules if m.enabled]),
                "modules": enhanced_modules,
            }

        except Exception as e:
            self.logger.error(f"Module discovery failed: {e}")
            return {"error": str(e), "modules": []}

    async def _fetch_modules(self, foundry_client) -> dict:
        """Fetch list of modules from Foundry."""
        try:
            # Execute a macro/script to get modules
            script = """
            const modules = game.modules.entries
                .map(([id, m]) => ({
                    id: id,
                    name: m.data.title,
                    version: m.data.version,
                    enabled: m.active,
                    description: m.data.description,
                }))
                .reduce((obj, m) => {obj[m.id] = m; return obj}, {});

            JSON.stringify(modules);
            """

            result = await foundry_client._send("execute-js", script=script, _timeout=10)

            # Parse the result
            if isinstance(result, str):
                import json

                return json.loads(result)
            return result

        except Exception as e:
            self.logger.warning(f"Could not fetch modules via script: {e}")
            return {}

    async def _enhance_module_info(self, module_id: str, module_data: dict) -> Optional[ModuleInfo]:
        """Enhance module info with known capabilities."""
        capabilities = self.MODULE_CAPABILITIES.get(module_id, {})

        if not capabilities:
            # Unknown module - still track it but with basic info
            return ModuleInfo(
                id=module_id,
                name=module_data.get("name", module_id),
                version=module_data.get("version", "unknown"),
                enabled=module_data.get("enabled", False),
                description=module_data.get("description", ""),
                capabilities=[],
                narrative_use_cases=[],
            )

        return ModuleInfo(
            id=module_id,
            name=capabilities.get("name", module_data.get("name", module_id)),
            version=module_data.get("version", "unknown"),
            enabled=module_data.get("enabled", False),
            description=module_data.get("description", ""),
            capabilities=capabilities.get("capabilities", []),
            narrative_use_cases=capabilities.get("narrative_uses", []),
        )


class ModuleSynergyMapper:
    """Maps modules to narrative elements for maximum engagement."""

    def __init__(self):
        self.logger = logger

    async def map_synergies(
        self, campaign_analysis: dict, available_modules: list[ModuleInfo]
    ) -> dict:
        """Map available modules to campaign narrative elements."""
        self.logger.info("Mapping module synergies to campaign elements...")

        enabled_modules = [m for m in available_modules if m.enabled]

        synergies = {
            "scene_enhancements": await self._map_scene_synergies(
                campaign_analysis.get("scenes", []), enabled_modules
            ),
            "encounter_enhancements": await self._map_encounter_synergies(
                campaign_analysis.get("encounters", []), enabled_modules
            ),
            "npc_enhancements": await self._map_npc_synergies(
                campaign_analysis.get("npcs", []), enabled_modules
            ),
            "narrative_arc_enhancements": await self._map_narrative_synergies(
                campaign_analysis.get("narrative_arcs", []), enabled_modules
            ),
            "immersion_gap_fills": await self._map_immersion_gaps(
                campaign_analysis.get("immersion_gaps", []), enabled_modules
            ),
        }

        return synergies

    async def _map_scene_synergies(self, scenes: list, modules: list[ModuleInfo]) -> list[dict]:
        """Map modules to individual scenes."""
        synergies = []

        for scene in scenes:
            scene_synergies = []

            # Check for animation modules
            if any(m.id == "animated-spell-effects-cartoon" for m in modules if m.enabled):
                if "combat" in scene.get("required_player_engagement", ""):
                    scene_synergies.append({
                        "module": "animated-spell-effects-cartoon",
                        "enhancement": "Add dramatic spell animations during combat",
                        "implementation": "Sync spell descriptions with ASE effects",
                    })

            # Check for notification modules
            if any(m.id == "notification" for m in modules if m.enabled):
                scene_synergies.append({
                    "module": "notification",
                    "enhancement": "Display atmospheric notifications",
                    "implementation": "Create immersive environmental awareness messages",
                })

            if scene_synergies:
                synergies.append({
                    "scene": scene.get("name"),
                    "synergies": scene_synergies,
                })

        return synergies

    async def _map_encounter_synergies(self, encounters: list, modules: list[ModuleInfo]) -> list[dict]:
        """Map modules to combat encounters."""
        synergies = []

        for encounter in encounters:
            encounter_synergies = []

            # Dice effects
            if any(m.id == "dice-so-nice" for m in modules if m.enabled):
                encounter_synergies.append({
                    "module": "dice-so-nice",
                    "enhancement": "Create dramatic dice moments",
                    "implementation": "Emphasize critical rolls with narrative beats",
                })

            # Better Rolls
            if any(m.id == "betterrolls5e" for m in modules if m.enabled):
                encounter_synergies.append({
                    "module": "betterrolls5e",
                    "enhancement": "Track roll consequences for narrative impact",
                    "implementation": "Link critical successes/failures to story outcomes",
                })

            # Combat Carousel
            if any(m.id == "combat-carousel" for m in modules if m.enabled):
                encounter_synergies.append({
                    "module": "combat-carousel",
                    "enhancement": "Tactical storytelling support",
                    "implementation": "Provide GM commentary on tactical developments",
                })

            if encounter_synergies:
                synergies.append({
                    "encounter": encounter.get("name"),
                    "synergies": encounter_synergies,
                })

        return synergies

    async def _map_npc_synergies(self, npcs: list, modules: list[ModuleInfo]) -> list[dict]:
        """Map modules to NPC interactions."""
        synergies = []

        for npc in npcs:
            npc_synergies = []

            # Notification for character thoughts
            if any(m.id == "notification" for m in modules if m.enabled):
                npc_synergies.append({
                    "module": "notification",
                    "enhancement": "Display NPC thoughts and motivations",
                    "implementation": "Show internal monologue during key interactions",
                })

            # Journal tabs for character development
            if any(m.id == "journal-entrypage-tabs" for m in modules if m.enabled):
                npc_synergies.append({
                    "module": "journal-entrypage-tabs",
                    "enhancement": "Organize NPC relationship trees",
                    "implementation": "Create branching character development paths",
                })

            if npc_synergies:
                synergies.append({
                    "npc": npc.get("name"),
                    "synergies": npc_synergies,
                })

        return synergies

    async def _map_narrative_synergies(self, arcs: list, modules: list[ModuleInfo]) -> list[dict]:
        """Map modules to narrative arcs."""
        synergies = []

        for arc in arcs:
            arc_synergies = []

            # Simple Calendar for time-based hooks
            if any(m.id == "simple-calendar" for m in modules if m.enabled):
                arc_synergies.append({
                    "module": "simple-calendar",
                    "enhancement": "Create time-based narrative progression",
                    "implementation": "Link story milestones to in-game dates",
                })

            # Journal Tabs for arc organization
            if any(m.id == "journal-entrypage-tabs" for m in modules if m.enabled):
                arc_synergies.append({
                    "module": "journal-entrypage-tabs",
                    "enhancement": "Organize interconnected quest narratives",
                    "implementation": "Create layered arc discovery system",
                })

            if arc_synergies:
                synergies.append({
                    "arc": arc.get("title"),
                    "synergies": arc_synergies,
                })

        return synergies

    async def _map_immersion_gaps(self, gaps: list, modules: list[ModuleInfo]) -> list[dict]:
        """Map modules to fill immersion gaps."""
        fills = []

        for gap in gaps:
            if "lighting" in gap.lower() and any(
                m.id == "animated-spell-effects-cartoon" for m in modules if m.enabled
            ):
                fills.append({
                    "gap": gap,
                    "module": "animated-spell-effects-cartoon",
                    "solution": "Use effect animations to enhance lighting atmosphere",
                })

            if "sound" in gap.lower() and any(
                m.id == "notification" for m in modules if m.enabled
            ):
                fills.append({
                    "gap": gap,
                    "module": "notification",
                    "solution": "Create audio descriptions via notifications",
                })

        return fills
