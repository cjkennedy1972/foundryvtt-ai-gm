"""Discover and map Foundry modules for campaign enhancement using LLM-driven analysis."""

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
    """Discovers installed modules in Foundry and uses LLM to understand their capabilities."""

    def __init__(self, llm_manager=None):
        self.logger = logger
        self.llm_manager = llm_manager

    async def discover_modules(self, foundry_client, llm_manager=None) -> dict:
        """Discover all installed modules and use LLM to understand their capabilities."""
        self.logger.info("Discovering installed modules in Foundry...")

        try:
            # Get module list from Foundry
            modules = await self._fetch_modules(foundry_client)

            if not modules:
                self.logger.warning("No modules found or discovery failed")
                return {"total_modules": 0, "enabled_modules": 0, "modules": []}

            # Use LLM to enhance module information
            llm_mgr = llm_manager or self.llm_manager
            enhanced_modules = []

            for module_id, module_data in modules.items():
                enhanced = await self._enhance_with_llm(
                    module_id, module_data, llm_mgr
                )
                if enhanced:
                    enhanced_modules.append(enhanced)

            self.logger.info(f"Discovered and analyzed {len(enhanced_modules)} modules")
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

    async def _enhance_with_llm(
        self, module_id: str, module_data: dict, llm_manager
    ) -> Optional[ModuleInfo]:
        """Use LLM to understand module capabilities and narrative uses."""

        if not llm_manager:
            # Fallback: return basic info without LLM enhancement
            return ModuleInfo(
                id=module_id,
                name=module_data.get("name", module_id),
                version=module_data.get("version", "unknown"),
                enabled=module_data.get("enabled", False),
                description=module_data.get("description", ""),
                capabilities=[],
                narrative_use_cases=[],
            )

        try:
            # Use LLM to analyze the module
            prompt = f"""
            Analyze this Foundry VTT module and provide structured output.

            Module ID: {module_id}
            Module Name: {module_data.get('name', 'Unknown')}
            Description: {module_data.get('description', 'No description')}

            Provide a JSON response with:
            {{
                "capabilities": ["capability1", "capability2", ...],
                "narrative_use_cases": ["use case 1", "use case 2", ...],
                "story_enhancement_potential": "high/medium/low"
            }}

            Capabilities should be concrete features the module provides.
            Narrative use cases should explain how it enhances D&D storytelling/immersion.
            """

            response = await llm_manager.generate_text(prompt)

            # Parse LLM response
            import json
            try:
                module_analysis = json.loads(response)
            except json.JSONDecodeError:
                # If LLM doesn't return JSON, extract what we can
                module_analysis = {
                    "capabilities": [],
                    "narrative_use_cases": [response[:100]],  # Use first 100 chars as fallback
                }

            return ModuleInfo(
                id=module_id,
                name=module_data.get("name", module_id),
                version=module_data.get("version", "unknown"),
                enabled=module_data.get("enabled", False),
                description=module_data.get("description", ""),
                capabilities=module_analysis.get("capabilities", []),
                narrative_use_cases=module_analysis.get("narrative_use_cases", []),
            )

        except Exception as e:
            self.logger.warning(f"LLM enhancement failed for {module_id}: {e}")
            # Return basic info as fallback
            return ModuleInfo(
                id=module_id,
                name=module_data.get("name", module_id),
                version=module_data.get("version", "unknown"),
                enabled=module_data.get("enabled", False),
                description=module_data.get("description", ""),
                capabilities=[],
                narrative_use_cases=[],
            )


class ModuleSynergyMapper:
    """Maps modules to narrative elements using LLM-driven analysis."""

    def __init__(self, llm_manager=None):
        self.logger = logger
        self.llm_manager = llm_manager

    async def map_synergies(
        self, campaign_analysis: dict, available_modules: list[ModuleInfo], llm_manager=None
    ) -> dict:
        """Map available modules to campaign narrative elements using LLM."""
        self.logger.info("Mapping module synergies to campaign elements...")

        enabled_modules = [m for m in available_modules if m.enabled]
        llm_mgr = llm_manager or self.llm_manager

        synergies = {
            "scene_enhancements": await self._map_scene_synergies(
                campaign_analysis.get("scenes", []), enabled_modules, llm_mgr
            ),
            "encounter_enhancements": await self._map_encounter_synergies(
                campaign_analysis.get("encounters", []), enabled_modules, llm_mgr
            ),
            "npc_enhancements": await self._map_npc_synergies(
                campaign_analysis.get("npcs", []), enabled_modules, llm_mgr
            ),
            "narrative_arc_enhancements": await self._map_narrative_synergies(
                campaign_analysis.get("narrative_arcs", []), enabled_modules, llm_mgr
            ),
            "immersion_gap_fills": await self._map_immersion_gaps(
                campaign_analysis.get("immersion_gaps", []), enabled_modules, llm_mgr
            ),
        }

        return synergies

    async def _map_scene_synergies(self, scenes: list, modules: list[ModuleInfo], llm_manager) -> list[dict]:
        """Map modules to individual scenes using LLM."""
        synergies = []

        for scene in scenes:
            if not llm_manager:
                continue

            try:
                # Handle both NarrativeElement objects and dicts
                scene_name = scene.name if hasattr(scene, 'name') else scene.get('name')
                scene_desc = scene.description if hasattr(scene, 'description') else scene.get('description', '')
                scene_engagement = scene.required_player_engagement if hasattr(scene, 'required_player_engagement') else scene.get('required_player_engagement', 'mixed')
                scene_drama = scene.drama_level if hasattr(scene, 'drama_level') else scene.get('drama_level', 5)

                prompt = f"""
                Scene: {scene_name}
                Description: {scene_desc}
                Engagement Type: {scene_engagement}
                Drama Level: {scene_drama}/10

                Available modules:
                {chr(10).join(f"- {m.name}: {', '.join(m.narrative_use_cases[:2])}" for m in modules)}

                Suggest which modules would enhance this scene and how.
                Format as JSON: {{"scene": "name", "synergies": [{{"module": "name", "enhancement": "...", "implementation": "..."}}]}}
                """

                response = await llm_manager.generate_text(prompt)

                import json
                try:
                    scene_synergy = json.loads(response)
                    if scene_synergy.get("synergies"):
                        synergies.append(scene_synergy)
                except json.JSONDecodeError:
                    pass

            except Exception as e:
                scene_name = scene.name if hasattr(scene, 'name') else scene.get('name', 'unknown')
                self.logger.warning(f"Failed to map scene synergies for {scene_name}: {e}")

        return synergies

    async def _map_encounter_synergies(self, encounters: list, modules: list[ModuleInfo], llm_manager) -> list[dict]:
        """Map modules to combat encounters using LLM."""
        synergies = []

        for encounter in encounters:
            if not llm_manager:
                continue

            try:
                # Handle both NarrativeElement objects and dicts
                enc_name = encounter.name if hasattr(encounter, 'name') else encounter.get('name')
                enc_drama = encounter.drama_level if hasattr(encounter, 'drama_level') else encounter.get('drama_level', 5)

                prompt = f"""
                Encounter: {enc_name}
                Drama Level: {enc_drama}/10

                Available modules:
                {chr(10).join(f"- {m.name}: {', '.join(m.narrative_use_cases[:2])}" for m in modules)}

                Suggest which modules would make this encounter more dramatic and engaging.
                Format as JSON: {{"encounter": "name", "synergies": [{{"module": "name", "enhancement": "...", "implementation": "..."}}]}}
                """

                response = await llm_manager.generate_text(prompt)

                import json
                try:
                    encounter_synergy = json.loads(response)
                    if encounter_synergy.get("synergies"):
                        synergies.append(encounter_synergy)
                except json.JSONDecodeError:
                    pass

            except Exception as e:
                enc_name = encounter.name if hasattr(encounter, 'name') else encounter.get('name', 'unknown')
                self.logger.warning(f"Failed to map encounter synergies for {enc_name}: {e}")

        return synergies

    async def _map_npc_synergies(self, npcs: list, modules: list[ModuleInfo], llm_manager) -> list[dict]:
        """Map modules to NPC interactions using LLM."""
        synergies = []

        for npc in npcs:
            if not llm_manager:
                continue

            try:
                # Handle both NarrativeElement objects and dicts
                npc_name = npc.name if hasattr(npc, 'name') else npc.get('name')
                npc_desc = npc.description if hasattr(npc, 'description') else npc.get('description', '')
                npc_drama = npc.drama_level if hasattr(npc, 'drama_level') else npc.get('drama_level', 5)

                prompt = f"""
                NPC: {npc_name}
                Description: {npc_desc}
                Drama Level: {npc_drama}/10

                Available modules:
                {chr(10).join(f"- {m.name}: {', '.join(m.narrative_use_cases[:2])}" for m in modules)}

                Suggest which modules would enhance NPC interactions and character development.
                Format as JSON: {{"npc": "name", "synergies": [{{"module": "name", "enhancement": "...", "implementation": "..."}}]}}
                """

                response = await llm_manager.generate_text(prompt)

                import json
                try:
                    npc_synergy = json.loads(response)
                    if npc_synergy.get("synergies"):
                        synergies.append(npc_synergy)
                except json.JSONDecodeError:
                    pass

            except Exception as e:
                npc_name = npc.name if hasattr(npc, 'name') else npc.get('name', 'unknown')
                self.logger.warning(f"Failed to map NPC synergies for {npc_name}: {e}")

        return synergies

    async def _map_narrative_synergies(self, arcs: list, modules: list[ModuleInfo], llm_manager) -> list[dict]:
        """Map modules to narrative arcs using LLM."""
        synergies = []

        for arc in arcs:
            if not llm_manager:
                continue

            try:
                # Handle both dict arcs (from campaign data) and other formats
                arc_title = arc.get('title') if isinstance(arc, dict) else getattr(arc, 'title', 'Unknown Arc')
                arc_progression = arc.get('progression', []) if isinstance(arc, dict) else getattr(arc, 'progression', [])

                prompt = f"""
                Narrative Arc: {arc_title}
                Story Stages: {len(arc_progression)}

                Available modules:
                {chr(10).join(f"- {m.name}: {', '.join(m.narrative_use_cases[:2])}" for m in modules)}

                Suggest which modules would enhance this narrative arc's progression and player engagement.
                Format as JSON: {{"arc": "title", "synergies": [{{"module": "name", "enhancement": "...", "implementation": "..."}}]}}
                """

                response = await llm_manager.generate_text(prompt)

                import json
                try:
                    arc_synergy = json.loads(response)
                    if arc_synergy.get("synergies"):
                        synergies.append(arc_synergy)
                except json.JSONDecodeError:
                    pass

            except Exception as e:
                arc_title = arc.get('title', 'unknown') if isinstance(arc, dict) else 'unknown'
                self.logger.warning(f"Failed to map arc synergies for {arc_title}: {e}")

        return synergies

    async def _map_immersion_gaps(self, gaps: list, modules: list[ModuleInfo], llm_manager) -> list[dict]:
        """Map modules to fill immersion gaps using LLM."""
        fills = []

        if not llm_manager or not gaps:
            return fills

        try:
            modules_desc = chr(10).join(f"- {m.name}: {', '.join(m.narrative_use_cases[:2])}" for m in modules)
            gaps_desc = chr(10).join(f"- {gap}" for gap in gaps)

            prompt = f"""
            Immersion gaps identified:
            {gaps_desc}

            Available modules:
            {modules_desc}

            Suggest which modules would best fill these immersion gaps.
            Format as JSON: {{"fills": [{{"gap": "description", "module": "name", "solution": "how to use it"}}]}}
            """

            response = await llm_manager.generate_text(prompt)

            import json
            try:
                result = json.loads(response)
                fills = result.get("fills", [])
            except json.JSONDecodeError:
                pass

        except Exception as e:
            self.logger.warning(f"Failed to map immersion gap fills: {e}")

        return fills
