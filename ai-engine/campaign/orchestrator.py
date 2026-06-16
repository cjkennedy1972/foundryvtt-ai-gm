"""
Campaign Orchestrator — High-level pipeline for building FoundryVTT campaigns.

Orchestrates the full campaign creation pipeline:
1. Scan the connected FoundryVTT world (detect scenes, actors, modules, capabilities)
2. Generate a complete campaign structure via LLM
3. Save campaign data to Obsidian vault
4. Generate map/portrait images via oMLX or ComfyUI
5. Deploy campaign elements to FoundryVTT (scenes, journal entries, NPCs, loot tables, quest logs)
6. Report progress to caller

Usage:
    from campaign.orchestrator import CampaignOrchestrator

    orch = CampaignOrchestrator()
    result = await orch.build_campaign(
        name="My New Campaign",
        prompt="A dark fantasy campaign about...",
        on_progress=callback,
    )
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import settings
from utils.path_safety import sanitize_filename

logger = logging.getLogger(__name__)


class CampaignOrchestrator:
    """Orchestrates the full campaign build pipeline."""

    def __init__(self, settings_obj=None):
        self.settings = settings_obj or settings

    # ─── Phase 1: Scan FoundryVTT world ─────────────────────────────────────

    async def scan_foundry_world(self, foundry_client) -> Dict[str, Any]:
        """Scan the currently connected FoundryVTT world.

        Detects scenes, actors, users, modules, and addon capabilities.
        Returns a catalog of existing content and available capabilities.
        """
        scan_result: Dict[str, Any] = {
            "scenes": [],
            "actors": [],
            "users": [],
            "rooms": [],
            "active_modules": {},   # {module_id: {title, version}}
            "capabilities": {},
        }

        # World info — active modules, system, users
        try:
            world_info = await foundry_client.get_world_info()
            for mod in world_info.get("modules", []):
                if mod.get("active"):
                    scan_result["active_modules"][mod["id"]] = {
                        "title": mod.get("title", mod["id"]),
                        "version": mod.get("version", ""),
                    }
            logger.info(f"Detected {len(scan_result['active_modules'])} active modules")
        except Exception as e:
            logger.warning(f"Failed to get world info: {e}")

        # Scan scenes
        try:
            scan_result["scenes"] = await foundry_client.get_scenes()
        except Exception as e:
            logger.warning(f"Failed to scan scenes: {e}")

        # Scan actors
        try:
            scan_result["actors"] = await foundry_client.get_actors()
        except Exception as e:
            logger.warning(f"Failed to scan actors: {e}")

        # Scan users
        try:
            scan_result["users"] = await foundry_client.get_users()
        except Exception as e:
            logger.warning(f"Failed to scan users: {e}")

        # Scan rooms
        try:
            scan_result["rooms"] = await foundry_client.get_rooms()
        except Exception as e:
            logger.warning(f"Failed to scan rooms: {e}")

        mods = scan_result["active_modules"]
        scan_result["capabilities"] = {
            "has_scenes": bool(scan_result["scenes"] or scan_result["rooms"]),
            "has_actors": bool(scan_result["actors"]),
            "has_users": bool(scan_result["users"]),
            # Animation
            "animated_tokens": "autoanimations" in mods and ("JB2A_DnD5e" in mods or "jb2a_patreon" in mods),
            # Combat
            "spell_automation": "midi-qol" in mods,
            "active_effects": "dae" in mods,
            # Items
            "item_piles": "item-piles" in mods,
            "loot_sheets": "lootsheet-simple" in mods,
            # Vision
            "vision_5e": "vision-5e" in mods,
            # Scenes
            "dynamic_soundscapes": "dynamic-soundscapes" in mods,
            "multi_floor_scenes": "levels" in mods,
            "fog_effects": "fog-weaver" in mods,
            "ingame_clock": "smalltime" in mods,
            "ingame_calendar": "foundryvtt-simple-calendar-reborn" in mods,
            # NPCs
            "npc_patrol": "patrol" in mods,
            "token_notes": "token-notes" in mods,
            # Quest / Narrative
            "progress_tracking": "progress-tracker" in mods,
            "quest_log": "rpgx-quest-log" in mods,
            # Language
            "polyglot": "polyglot" in mods,
            # Conditions / Traits
            "condition_tracking": "mmm" in mods,
        }

        return scan_result

    # ─── Phase 2: Generate campaign via LLM ─────────────────────────────────

    async def generate_campaign_data(
        self,
        prompt: str,
        llm_client,
        scan_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate complete campaign data using the LLM."""
        from campaign.generator import (
            generate_campaign_prompt,
            parse_campaign_response,
            validate_campaign,
        )

        # Build enhanced prompt with scan results if available
        active_modules = scan_result.get("active_modules", {}) if scan_result else {}
        scan_info = ""
        if scan_result and (scan_result.get("scenes") or scan_result.get("actors")):
            existing_scenes = [s.get("name", "Unknown") for s in scan_result.get("scenes", [])]
            existing_actors = [a.get("name", "Unknown") for a in scan_result.get("actors", [])]
            scan_info = (
                "\n\n## Current FoundryVTT World Context\n"
                f"- Existing scenes: {', '.join(existing_scenes) if existing_scenes else '(none)'}\n"
                f"- Existing actors: {', '.join(existing_actors) if existing_actors else '(none)'}\n"
                f"- Users online: {len(scan_result.get('users', []))}\n"
                "Build the new campaign alongside or in addition to this existing content.\n"
            )

        prompt_text = generate_campaign_prompt(prompt, active_modules=active_modules) + scan_info

        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": prompt},
        ]

        endpoint = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": 0.85,
            "max_tokens": 32768,
        }
        # Disable thinking for Qwen3 models so all tokens go to JSON output.
        # /nothink works at the tokenizer level; enable_thinking=False is the API param.
        if "Qwen" in (self.settings.model or ""):
            payload["enable_thinking"] = False
            messages[-1]["content"] = "/nothink\n" + messages[-1]["content"]

        resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=600)
        if resp.status_code != 200:
            raise Exception(f"LLM request failed: {resp.status_code} {resp.text[:500]}")

        raw_text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        campaign_data = parse_campaign_response(raw_text)
        campaign_data["generated_prompt"] = prompt
        campaign_data["generated_at"] = time.strftime("%Y-%m-%d %H:%M")

        # Validate
        warnings = validate_campaign(campaign_data)
        if warnings:
            for w in warnings:
                logger.warning(f"Campaign validation warning: {w}")
        campaign_data["validation_warnings"] = warnings

        return campaign_data

    # ─── Phase 3: Save to Obsidian vault ────────────────────────────────────

    async def save_to_vault(
        self, campaign_data: Dict[str, Any], vault_path: str = None
    ) -> Dict[str, Any]:
        """Save campaign data to Obsidian vault."""
        if vault_path is None:
            vault_path = settings.campaign_vault_path

        from campaign.obsidian_sync import sync_campaign_to_vault

        manifest = await sync_campaign_to_vault(campaign_data, vault_path)
        return manifest

    # ─── Phase 4: Generate maps and portraits ────────────────────────────────

    def _build_scene_prompt(self, scene: Dict[str, Any]) -> str:
        """Build a rich map prompt from scene data when map_style is not provided."""
        scene_type = scene.get("type", "fantasy")
        scene_name = scene.get("name", "Scene")
        description = scene.get("description", "")
        atmosphere = scene.get("atmosphere", "")
        lighting = scene.get("lighting", "warm light")

        # Build a detailed prompt from available data
        type_perspectives = {
            "settlement": "top-down settlement interior",
            "tavern": "top-down tavern interior",
            "dungeon": "top-down dungeon",
            "cave": "top-down cavern",
            "forest": "top-down forest clearing",
            "temple": "top-down temple interior",
            "castle": "top-down castle room",
            "shop": "top-down shop interior",
            "crypt": "top-down crypt",
            "ruins": "top-down ancient ruins",
            "village": "isometric village scene",
            "city": "isometric city district",
            "wilderness": "aerial wilderness view",
        }
        perspective = type_perspectives.get(scene_type, f"top-down {scene_type}")

        # Extract key details from description
        prompt_parts = [perspective]

        # Add atmospheric details
        if atmosphere:
            prompt_parts.append(f"{atmosphere} atmosphere")
        if lighting:
            prompt_parts.append(f"{lighting} lighting")

        # Add description keywords (pick 2-3 strongest keywords)
        if description:
            # Extract nouns/adjectives from description
            key_words = [w for w in description.split() if len(w) > 4 and w[0].isupper()][:3]
            if key_words:
                prompt_parts.extend(key_words)

        # Add visual enhancement
        prompt_parts.append("detailed visual, fantasy illustration style")

        return ", ".join(prompt_parts)

    def _build_location_prompt(self, location: Dict[str, Any]) -> str:
        """Build a rich map prompt from location data when map_style is not provided."""
        location_type = location.get("type", "fantasy")
        location_name = location.get("name", "Location")
        description = location.get("description", "")
        key_features = location.get("key_features", [])

        # Build a detailed prompt from available data
        type_perspectives = {
            "settlement": "isometric village overview",
            "village": "isometric village overview",
            "town": "isometric town overview",
            "city": "isometric city overview",
            "dungeon": "top-down dungeon complex",
            "cave": "aerial cavern system",
            "cave_system": "aerial cavern system",
            "forest": "aerial forest with clearings",
            "wilderness": "aerial wilderness map",
            "castle": "isometric fortress overview",
            "fortress": "isometric fortress overview",
            "temple": "isometric temple grounds",
            "ruin": "top-down ancient ruin complex",
            "ruins": "top-down ancient ruin complex",
            "crypt": "top-down crypt complex",
            "tower": "isometric tower overview",
            "keep": "isometric keep overview",
        }
        perspective = type_perspectives.get(location_type, f"isometric {location_type} overview")

        # Build prompt
        prompt_parts = [perspective]

        # Add key features as visual elements
        if key_features:
            features = ", ".join(key_features[:4])  # Pick up to 4 features
            prompt_parts.append(features)

        # Add atmosphere from description
        if description:
            # Extract 2-3 key adjectives from description
            key_words = [w for w in description.split() if len(w) > 5 and w[0].isupper()][:2]
            if key_words:
                prompt_parts.extend(key_words)

        # Add visual style
        prompt_parts.append("detailed aerial view, fantasy cartography style, vibrant colors")

        return ", ".join(prompt_parts)

    async def generate_assets(
        self,
        campaign_data: Dict[str, Any],
        map_generator,
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Generate all map and portrait images for the campaign."""
        output_dir.mkdir(parents=True, exist_ok=True)
        results = {
            "maps": [],
            "portraits": [],
            "status": "completed",
        }

        # Generate maps for scenes
        scenes = campaign_data.get("scenes", [])
        location_scenes = [s for s in scenes if s.get("map_needed")]

        if location_scenes:
            logger.info(f"Generating {len(location_scenes)} scene map(s)...")
            for scene in location_scenes:
                prompt = scene.get("map_style", self._build_scene_prompt(scene))
                try:
                    map_result = await map_generator.generate_map(
                        prompt=prompt,
                        output_dir=output_dir,
                    )
                    if map_result["status"] == "success":
                        results["maps"].append({
                            "scene": scene["name"],
                            "type": "scene_map",
                            "file": map_result["output_file"],
                            "provider": map_result.get("provider", "unknown"),
                        })
                        scene["map_file"] = Path(map_result["output_file"]).name
                    else:
                        logger.warning(f"Map generation failed for {scene['name']}: {map_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Map generation error for {scene['name']}: {e}")

        # Generate maps for locations
        locations = campaign_data.get("locations", [])
        location_maps = [l for l in locations if l.get("map_needed")]

        if location_maps:
            logger.info(f"Generating {len(location_maps)} location map(s)...")
            for loc in location_maps:
                prompt = loc.get("map_style", self._build_location_prompt(loc))
                try:
                    map_result = await map_generator.generate_map(
                        prompt=prompt,
                        output_dir=output_dir,
                    )
                    if map_result["status"] == "success":
                        results["maps"].append({
                            "location": loc["name"],
                            "type": "location_map",
                            "file": map_result["output_file"],
                            "provider": map_result.get("provider", "unknown"),
                        })
                        loc["map_file"] = Path(map_result["output_file"]).name
                    else:
                        logger.warning(f"Map generation failed for {loc['name']}: {map_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Map generation error for {loc['name']}: {e}")

        # Generate NPC portraits
        npcs = campaign_data.get("npcs", [])
        portrait_npcs = [n for n in npcs if n.get("portrait_needed")]

        if portrait_npcs:
            logger.info(f"Generating {len(portrait_npcs)} NPC portrait(s)...")
            portraits_dir = output_dir / "portraits"
            portraits_dir.mkdir(exist_ok=True)

            for npc in portrait_npcs:
                prompt = npc.get("description", f"{npc.get('name', 'NPC')} portrait")
                portrait_file = portraits_dir / f"portrait_{npc['name'].replace(' ', '_').lower()}.png"

                try:
                    portrait_result = await map_generator.generate_portrait(
                        prompt=prompt,
                        output_dir=portraits_dir,
                    )
                    if portrait_result["status"] == "success":
                        results["portraits"].append({
                            "npc": npc["name"],
                            "file": portrait_result["output_file"],
                            "provider": portrait_result.get("provider", "unknown"),
                        })
                        npc["portrait_file"] = Path(portrait_result["output_file"]).name
                    else:
                        logger.warning(f"Portrait generation failed for {npc['name']}: {portrait_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Portrait generation error for {npc['name']}: {e}")

        results["total_maps"] = len(results["maps"])
        results["total_portraits"] = len(results["portraits"])
        return results

    # ─── Regenerate assets for an existing campaign ─────────────────────────

    async def regenerate_assets_for_campaign(
        self,
        campaign_name: str,
        foundry_client=None,
        comfyui_url: str = None,
        attach_to_foundry: bool = True,
        progress: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Regenerate maps/portraits for an already-built campaign.

        Loads the campaign from the vault, regenerates images with the current
        (improved) map generator, persists them, and — when Foundry is connected
        — uploads each map and attaches it as the background of the matching scene
        (updating existing scenes by name, so nothing is duplicated). Does NOT
        re-run the LLM; all existing NPCs/quests/story are preserved.
        """
        from campaign.map_generator import MapGenerator
        from campaign.obsidian_sync import get_campaign_folder, resolve_vault_path

        def _progress(msg: str, **kw):
            if progress:
                progress(msg, **kw)
            logger.info(msg)

        summary: Dict[str, Any] = {
            "campaign_name": campaign_name,
            "maps_generated": 0,
            "portraits_generated": 0,
            "scenes_attached": 0,
            "errors": [],
            "status": "completed",
        }

        # ── Load campaign.json from the vault ──
        vault = resolve_vault_path(self.settings.campaign_vault_path)
        folder = get_campaign_folder(vault, campaign_name)
        campaign_file = folder / "campaign.json"
        if not campaign_file.exists():
            summary["status"] = "error"
            summary["errors"].append(f"Campaign '{campaign_name}' not found in vault")
            return summary

        raw = await asyncio.to_thread(campaign_file.read_text, encoding="utf-8")
        campaign_data = json.loads(raw)

        # ── Generate images (improved SDXL workflow) ──
        safe_name = sanitize_filename(campaign_name.lower())
        asset_output_dir = Path("./campaign_assets") / (safe_name + "_maps")

        map_generator = MapGenerator(
            comfyui_url=comfyui_url or getattr(self.settings, "comfyui_url", "http://127.0.0.1:18188"),
        )
        try:
            if not (await map_generator.health_check()).get("comfyui"):
                summary["status"] = "error"
                summary["errors"].append("ComfyUI is not reachable")
                return summary

            _progress("🎨 Regenerating maps and portraits...", step="assets")
            asset_info = await self.generate_assets(campaign_data, map_generator, asset_output_dir)
            summary["maps_generated"] = asset_info.get("total_maps", 0)
            summary["portraits_generated"] = asset_info.get("total_portraits", 0)

            # ── Upload maps + attach to existing Foundry scenes (by name) ──
            connected = bool(foundry_client and getattr(foundry_client, "is_connected", False))
            if attach_to_foundry and connected:
                for scene in campaign_data.get("scenes", []):
                    map_file = scene.get("map_file")
                    if not map_file:
                        continue
                    img_path = asset_output_dir / map_file
                    if not img_path.exists():
                        continue
                    try:
                        img_bytes = await asyncio.to_thread(img_path.read_bytes)
                        upload = await foundry_client.upload_file(
                            file_bytes=img_bytes,
                            path=f"ai-gm-maps/{safe_name}",
                            filename=map_file,
                            mime_type="image/png",
                        )
                        # Prefer the path the relay reports; fall back to a constructed one.
                        src = (
                            (upload.get("path") if isinstance(upload, dict) else None)
                            or f"ai-gm-maps/{safe_name}/{map_file}"
                        )
                        scene["background_src"] = src
                        # FoundryVTT v14: Attach background via the Levels system
                        try:
                            logger.info(f"Fetching scene '{scene['name']}' to update levels...")
                            current_scene = await foundry_client.get_scene_by_name(scene["name"])
                            if not current_scene:
                                msg = f"scene '{scene['name']}': scene not found in Foundry"
                                logger.warning(msg)
                                summary["errors"].append(msg)
                                continue

                            logger.info(f"Current scene data keys: {list(current_scene.keys())}")

                            # Ensure scene has a levels array; if not, create one with a default level
                            levels = current_scene.get("levels", [])
                            logger.info(f"Scene has {len(levels)} level(s): {[l.get('name', 'unnamed') for l in levels]}")

                            if not levels:
                                logger.info(f"No levels found, creating default level")
                                levels = [{"name": "Base Level"}]

                            # Update the first (base) level's background
                            if levels:
                                levels[0]["background"] = {"src": src}
                                logger.info(f"Updated level '{levels[0].get('name')}' background to {src}")

                                # Send the updated levels back
                                logger.info(f"Sending update-scene for '{scene['name']}'...")
                                result = await foundry_client.update_scene(
                                    scene["name"],
                                    {"levels": levels}
                                )
                                logger.info(f"Update-scene result: {result}")
                                summary["scenes_attached"] += 1
                            else:
                                msg = f"scene '{scene['name']}': no levels array"
                                logger.warning(msg)
                                summary["errors"].append(msg)
                        except Exception as e:
                            msg = f"scene '{scene['name']}': {type(e).__name__}: {e}"
                            logger.exception(f"Scene attachment failed: {msg}")
                            summary["errors"].append(msg)
                    except Exception as e:
                        msg = f"scene '{scene.get('name', '?')}': {type(e).__name__}: {e}"
                        logger.exception(f"File upload/processing failed: {msg}")
                        summary["errors"].append(msg)
            elif attach_to_foundry and not connected:
                summary["errors"].append(
                    "Foundry not connected — images regenerated and saved, but not attached to scenes"
                )
        finally:
            await map_generator.close()

        # ── Persist updated references back to the vault ──
        await asyncio.to_thread(
            campaign_file.write_text,
            json.dumps(campaign_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _progress(
            f"✅ Regenerated {summary['maps_generated']} map(s), "
            f"{summary['portraits_generated']} portrait(s), "
            f"attached {summary['scenes_attached']} to Foundry",
            step="assets",
        )
        return summary

    # ─── Phase 5: Deploy to FoundryVTT ──────────────────────────────────────

    async def deploy_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_info: Dict[str, Any],
        scan_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Deploy campaign elements to the connected FoundryVTT world.

        Uses active module information from scan_result to apply addon-specific
        flags and create enhanced entities (Item Piles, Playlists, animated NPCs, etc.)
        """
        mods: Dict[str, Any] = (scan_result or {}).get("active_modules", {})

        deployment: Dict[str, Any] = {
            "scenes": [],
            "npcs": [],
            "journal_entries": [],
            "quest_logs": [],
            "loot_tables": [],
            "loot_piles": [],
            "playlists": [],
            "calendar_events": [],
            "status": "complete",
        }

        async def _create(entity_type: str, data: dict) -> dict:
            result = await foundry_client._send("create", entityType=entity_type, data=data)
            return result.get("data", result) if isinstance(result, dict) else {}

        def _uuid(result: dict) -> str:
            return result.get("uuid", result.get("_id", ""))

        # ── NPCs ──────────────────────────────────────────────────────────────
        npcs = campaign_data.get("npcs", [])
        if npcs:
            logger.info(f"Deploying {len(npcs)} NPCs...")
            for npc in npcs:
                try:
                    npc_flags: Dict[str, Any] = {
                        "ai-gm": {
                            "faction": npc.get("faction", ""),
                            "stat_block": npc.get("stat_block", ""),
                            "npc_type": npc.get("npc_type", "combat"),
                        }
                    }

                    # Automated Animations
                    if "autoanimations" in mods and npc.get("animation_type", "none") != "none":
                        npc_flags["autoanimations"] = {"killAnim": False}

                    # Item Piles — merchant storefront
                    if "item-piles" in mods and npc.get("npc_type") == "merchant":
                        npc_flags["item-piles"] = {
                            "data": {
                                "enabled": True,
                                "type": "merchant",
                                "displayOne": False,
                                "showItemName": True,
                                "isMerchant": True,
                                "canInspectItems": True,
                            }
                        }

                    # Loot Sheet Simple (fallback to item-piles)
                    if "lootsheet-simple" in mods and "item-piles" not in mods and npc.get("npc_type") == "merchant":
                        npc_flags["lootsheet-simple"] = {"lootsheettype": "Merchant"}

                    # Midi QOL — spell automation flags
                    if "midi-qol" in mods:
                        midi_flags: Dict[str, Any] = {}
                        if npc.get("concentration_caster"):
                            midi_flags["concentration-automation"] = True
                        if npc.get("critical_threshold", 20) != 20:
                            midi_flags["critThreshold"] = npc["critical_threshold"]
                        if midi_flags:
                            npc_flags["midi-qol"] = midi_flags

                    # Token Notes — GM-only secret information
                    prototype_token: Dict[str, Any] = {}
                    if "token-notes" in mods and npc.get("gm_token_note"):
                        prototype_token.setdefault("flags", {})["token-notes"] = {
                            "note": npc["gm_token_note"]
                        }

                    # Patrol — guard NPCs
                    if "patrol" in mods and npc.get("npc_type") == "guard":
                        prototype_token.setdefault("flags", {})["patrol"] = {
                            "active": True, "speed": 1, "pause": 3000
                        }

                    # Build items list
                    items: List[Dict[str, Any]] = []

                    # Automated Animations / JB2A — weapon items for animation matching
                    if "autoanimations" in mods:
                        for weapon_name in npc.get("weapon_items", []):
                            item: Dict[str, Any] = {
                                "name": weapon_name,
                                "type": "weapon",
                                "system": {
                                    "description": {"value": ""},
                                    "quantity": 1,
                                    "equipped": True,
                                },
                            }
                            # Midi QOL attack bonus
                            if "midi-qol" in mods and npc.get("attack_bonus") is not None:
                                item["system"]["attackBonus"] = str(npc["attack_bonus"])
                            items.append(item)

                    # Midi QOL — spell items
                    if "midi-qol" in mods:
                        for spell in npc.get("spells", []):
                            if not isinstance(spell, dict) or not spell.get("name"):
                                continue
                            spell_item: Dict[str, Any] = {
                                "name": spell["name"],
                                "type": "spell",
                                "system": {
                                    "description": {"value": ""},
                                    "level": spell.get("level", 0),
                                    "school": spell.get("school", "evocation"),
                                    "range": {"value": spell.get("range", 0), "units": "ft"},
                                    "concentration": spell.get("concentration", False),
                                    "prepared": True,
                                },
                                "flags": {"midi-qol": {"onUseMacroName": ""}},
                            }
                            if spell.get("damage"):
                                spell_item["system"]["damage"] = {
                                    "parts": [[spell["damage"], spell.get("damage_type", "")]],
                                }
                            if spell.get("save"):
                                spell_item["system"]["save"] = {
                                    "ability": spell["save"],
                                    "dc": spell.get("save_dc", 13),
                                    "scaling": "flat",
                                }
                            if spell.get("aoe"):
                                spell_item["system"]["target"] = {
                                    "type": spell["aoe"].get("type", "sphere"),
                                    "value": spell["aoe"].get("size", 10),
                                    "units": "ft",
                                }
                            items.append(spell_item)

                    # Build system block
                    system_block: Dict[str, Any] = {
                        "details": {
                            "alignment": npc.get("alignment", ""),
                            "biography": {"value": npc.get("description", "")},
                            "cr": npc.get("cr", 1),
                        },
                        "attributes": {
                            "hp": {
                                "value": npc.get("hp", 10),
                                "max": npc.get("hp", 10),
                                "formula": npc.get("hp_formula", ""),
                            },
                            "ac": {
                                "flat": npc.get("ac", 10),
                                "calc": "natural",
                            },
                            "speed": {"value": npc.get("speed", 30), "units": "ft"},
                        },
                        "traits": {
                            "ci": {"value": npc.get("condition_immunities", [])},
                            "dv": {"value": npc.get("damage_vulnerabilities", [])},
                            "dr": {"value": npc.get("damage_resistances", [])},
                            "di": {"value": npc.get("damage_immunities", [])},
                            "languages": {
                                "value": npc.get("languages", []),
                                "custom": "",
                            },
                        },
                    }

                    # Vision 5e — senses
                    if "vision-5e" in mods and npc.get("senses"):
                        senses = npc["senses"]
                        system_block["attributes"]["senses"] = {
                            "darkvision": senses.get("darkvision", 0),
                            "blindsight": senses.get("blindsight", 0),
                            "tremorsense": senses.get("tremorsense", 0),
                            "truesight": senses.get("truesight", 0),
                            "units": "ft",
                        }

                    data: Dict[str, Any] = {
                        "name": npc["name"],
                        "type": "npc",
                        "system": system_block,
                        "flags": npc_flags,
                    }

                    # Dynamic Active Effects (DAE)
                    if "dae" in mods and isinstance(npc.get("active_effects"), list):
                        data["effects"] = [
                            {
                                "label": ae.get("label", ""),
                                "icon": ae.get("icon", "icons/svg/aura.svg"),
                                "description": ae.get("description", ""),
                                "disabled": False,
                                "transfer": True,
                                "changes": [],
                            }
                            for ae in npc["active_effects"]
                        ]

                    if items:
                        data["items"] = items
                    if prototype_token:
                        data["prototypeToken"] = prototype_token

                    result = await _create("Actor", data)
                    deployment["npcs"].append({"name": npc["name"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    npc_name = npc.get("name", "?")
                    logger.warning(f"Failed to create NPC {npc_name}: {e}")
                    deployment["npcs"].append({"name": npc_name, "status": "failed", "error": str(e)})

        # ── Journal Entries ───────────────────────────────────────────────────
        journal_entries = campaign_data.get("journal_entries", [])
        if journal_entries:
            logger.info(f"Deploying {len(journal_entries)} journal entries...")
            for entry in journal_entries:
                try:
                    entry_flags: Dict[str, Any] = {
                        "ai-gm": {"type": entry.get("type", "note"), "act": entry.get("act", 1)}
                    }
                    # Polyglot — in-world texts (ancient tomes, foreign letters)
                    if "polyglot" in mods and entry.get("language"):
                        entry_flags["polyglot"] = {"language": entry["language"]}
                    data = {
                        "name": entry["title"],
                        "pages": [{"name": entry["title"], "type": "text", "text": {"content": entry.get("body", ""), "format": 1}}],
                        "flags": entry_flags,
                    }
                    result = await _create("JournalEntry", data)
                    deployment["journal_entries"].append({"title": entry["title"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    entry_title = entry.get("title", "?")
                    logger.warning(f"Failed to create journal entry {entry_title}: {e}")
                    deployment["journal_entries"].append({"title": entry_title, "status": "failed", "error": str(e)})

        # ── Quest Logs ────────────────────────────────────────────────────────
        quest_logs = campaign_data.get("quest_logs", [])
        if quest_logs:
            logger.info(f"Deploying {len(quest_logs)} quest logs...")
            for quest in quest_logs:
                try:
                    objectives_html = "".join(
                        f"<li>{o.get('desc', o) if isinstance(o, dict) else o}"
                        + (f" <em>({o['check']})</em>" if isinstance(o, dict) and o.get("check") else "")
                        + "</li>"
                        for o in quest.get("objectives", [])
                    )
                    rewards_html = "".join(f"<li>{r}</li>" for r in quest.get("rewards", []))
                    body = (
                        f"<h2>{quest['title']}</h2>"
                        f"<p>{quest.get('description', '')}</p>"
                        f"<h3>Objectives</h3><ul>{objectives_html}</ul>"
                        f"<h3>Rewards</h3><ul>{rewards_html}</ul>"
                    )
                    quest_flags: Dict[str, Any] = {
                        "ai-gm": {
                            "quest_id": quest.get("id", ""),
                            "status": quest.get("status", "not-started"),
                            "act": quest.get("act", 1),
                        }
                    }
                    # Progress Tracker
                    if "progress-tracker" in mods:
                        quest_flags["progress-tracker"] = {
                            "enabled": True,
                            "status": quest.get("status", "not-started"),
                            "objectives": len(quest.get("objectives", [])),
                            "completed": 0,
                        }
                    # RPG-X Quest Log — rich quest metadata
                    if "rpgx-quest-log" in mods:
                        quest_flags["rpgx-quest-log"] = {
                            "questGiver": quest.get("quest_giver", ""),
                            "location": quest.get("location", ""),
                            "difficulty": quest.get("difficulty", "medium"),
                            "xpReward": quest.get("xp_reward", 0),
                            "timeLimitDays": quest.get("time_limit_days", 0),
                            "calendarDueDate": quest.get("calendar_due_date", {}),
                        }
                    data = {
                        "name": f"[Quest] {quest['title']}",
                        "pages": [{"name": quest["title"], "type": "text", "text": {"content": body, "format": 1}}],
                        "flags": quest_flags,
                    }
                    result = await _create("JournalEntry", data)
                    deployment["quest_logs"].append({"title": quest["title"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    quest_title = quest.get("title", "?")
                    logger.warning(f"Failed to create quest {quest_title}: {e}")
                    deployment["quest_logs"].append({"title": quest_title, "status": "failed", "error": str(e)})

        # ── Loot Tables (RollTable) + Item Piles ─────────────────────────────
        loot_tables = campaign_data.get("loot_tables", [])
        if loot_tables:
            logger.info(f"Deploying {len(loot_tables)} loot tables...")
            for table in loot_tables:
                # Always create the RollTable
                try:
                    roll_results = []
                    cumulative = 0
                    for e in table.get("entries", []):
                        w = e.get("weight", 1)
                        roll_results.append({
                            "type": 0,
                            "text": e.get("name", ""),
                            "weight": w,
                            "range": [cumulative + 1, cumulative + w],
                            "drawn": False,
                        })
                        cumulative += w
                    data = {
                        "name": table["name"],
                        "description": table.get("description", ""),
                        "results": roll_results,
                        "formula": f"1d{max(cumulative, 1)}",
                    }
                    result = await _create("RollTable", data)
                    deployment["loot_tables"].append({"name": table["name"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    logger.warning(f"Failed to create loot table {table.get('name', '?')}: {e}")
                    deployment["loot_tables"].append({"name": table.get("name", "?"), "status": "failed", "error": str(e)})

                # Item Piles — also create a physical loot container actor
                if "item-piles" in mods and table.get("deploy_as_pile", True):
                    try:
                        pile_items = []
                        for e in table.get("entries", []):
                            pile_items.append({
                                "name": e.get("name", "Loot"),
                                "type": e.get("foundry_item_type", "loot"),
                                "system": {
                                    "description": {"value": e.get("description", "")},
                                    "quantity": e.get("quantity", 1),
                                    "weight": e.get("weight_lbs", 0.1),
                                    "price": {
                                        "value": e.get("value_gp", 0),
                                        "denomination": "gp",
                                    },
                                    "rarity": e.get("rarity", "common"),
                                },
                            })
                        pile_actor = {
                            "name": f"{table['name']} (Loot)",
                            "type": "npc",
                            "items": pile_items,
                            "flags": {
                                "item-piles": {
                                    "data": {
                                        "enabled": True,
                                        "type": table.get("pile_type", "pile"),
                                        "displayOne": len(pile_items) == 1,
                                        "showItemName": True,
                                        "canInspectItems": True,
                                    }
                                },
                                "ai-gm": {"loot_table": table["name"]},
                            },
                        }
                        pile_result = await _create("Actor", pile_actor)
                        deployment["loot_piles"].append({"name": table["name"], "uuid": _uuid(pile_result), "status": "created"})
                    except Exception as e:
                        logger.warning(f"Failed to create Item Pile for {table.get('name', '?')}: {e}")
                        deployment["loot_piles"].append({"name": table.get("name", "?"), "status": "failed", "error": str(e)})

        # ── Scenes ────────────────────────────────────────────────────────────
        scenes = campaign_data.get("scenes", [])
        if scenes:
            logger.info(f"Deploying {len(scenes)} scenes...")
            for scene in scenes:
                try:
                    scene_flags: Dict[str, Any] = {
                        "ai-gm": {
                            "type": scene.get("type", "scene"),
                            "act": scene.get("act", 1),
                            "atmosphere": scene.get("atmosphere", ""),
                        }
                    }

                    # Dynamic Soundscapes
                    if "dynamic-soundscapes" in mods and scene.get("soundscape", "none") != "none":
                        scene_flags["dynamic-soundscapes"] = {
                            "ambient": True,
                            "preset": scene.get("soundscape", ""),
                        }

                    # Levels — multi-floor scenes
                    if "levels" in mods and scene.get("has_multiple_floors") and scene.get("floors"):
                        scene_flags["levels"] = {"sceneLevels": scene["floors"]}

                    # Better Roofs
                    if "betterroofs" in mods and scene.get("has_roof"):
                        scene_flags["betterroofs"] = {"roofEnabled": True}

                    # Fog Weaver — atmospheric fog overlays
                    if "fog-weaver" in mods and scene.get("fog_type", "none") != "none":
                        scene_flags["fog-weaver"] = {
                            "fogType": scene.get("fog_type", "light_fog"),
                            "fogDensity": scene.get("fog_density", 0.2),
                            "enabled": True,
                        }

                    # SmallTime — in-world time-of-day display
                    if "smalltime" in mods and scene.get("time_of_day") is not None:
                        scene_flags["smalltime"] = {
                            "timeOfDay": scene.get("time_of_day", 12),
                            "timePeriod": scene.get("time_period", "afternoon"),
                        }

                    data = {
                        "name": scene["name"],
                        "darkness": scene.get("darkness", 0.0),
                        "flags": scene_flags,
                    }
                    # FoundryVTT v14: Scenes use a Levels system. Create with a default level.
                    # If we have a background image reference, attach it to the level.
                    background_src = scene.get("background_src")
                    levels = [
                        {
                            "name": "Base Level",
                            "background": {"src": background_src} if background_src else {},
                        }
                    ]
                    data["levels"] = levels
                    result = await _create("Scene", data)
                    deployment["scenes"].append({"name": scene["name"], "uuid": _uuid(result), "status": "created"})
                except Exception as e:
                    logger.warning(f"Failed to create scene {scene.get('name', '?')}: {e}")
                    deployment["scenes"].append({"name": scene.get("name", "?"), "status": "failed", "error": str(e)})

        # ── Calendar Events (Simple Calendar Reborn) ─────────────────────────
        if "foundryvtt-simple-calendar-reborn" in mods:
            calendar_events = campaign_data.get("calendar_events", [])
            if calendar_events:
                logger.info(f"Deploying {len(calendar_events)} calendar events...")
                deployment["calendar_events"] = []
                for event in calendar_events:
                    try:
                        visible = event.get("visible_to_players", True)
                        body = (
                            f"<p>{event.get('description', '')}</p>"
                            f"<p><em>Type: {event.get('type', 'event')}</em></p>"
                        )
                        cal_flags: Dict[str, Any] = {
                            "foundryvtt-simple-calendar-reborn": {
                                "noteData": {
                                    "year": event.get("year", 1),
                                    "month": event.get("month", 1) - 1,
                                    "day": event.get("day", 1) - 1,
                                    "allDay": True,
                                    "playerVisible": visible,
                                    "categories": [event.get("type", "event")],
                                }
                            },
                            "ai-gm": {"type": "calendar_event"},
                        }
                        data = {
                            "name": event["title"],
                            "pages": [{"name": event["title"], "type": "text", "text": {"content": body, "format": 1}}],
                            "flags": cal_flags,
                        }
                        result = await _create("JournalEntry", data)
                        deployment["calendar_events"].append({"title": event["title"], "uuid": _uuid(result), "status": "created"})
                    except Exception as e:
                        logger.warning(f"Failed to create calendar event {event.get('title', '?')}: {e}")
                        deployment["calendar_events"].append({"title": event.get("title", "?"), "status": "failed", "error": str(e)})

        # ── Playlists (Dynamic Soundscapes) ───────────────────────────────────
        if "dynamic-soundscapes" in mods or "moulinette-soundboards" in mods:
            playlists = campaign_data.get("playlists", [])
            if playlists:
                logger.info(f"Deploying {len(playlists)} playlists...")
                for pl in playlists:
                    try:
                        pl_flags: Dict[str, Any] = {
                            "ai-gm": {"scene": pl.get("scene", ""), "mood": pl.get("mood", "")},
                        }
                        if "dynamic-soundscapes" in mods:
                            pl_flags["dynamic-soundscapes"] = {"ambient": True}
                        data = {
                            "name": pl["name"],
                            "mode": 1,       # sequential
                            "fade": 1000,
                            "description": pl.get("mood", ""),
                            "sounds": [],    # GM adds actual audio files via Foundry UI
                            "flags": pl_flags,
                        }
                        result = await _create("Playlist", data)
                        deployment["playlists"].append({"name": pl["name"], "uuid": _uuid(result), "status": "created"})
                    except Exception as e:
                        logger.warning(f"Failed to create playlist {pl.get('name', '?')}: {e}")
                        deployment["playlists"].append({"name": pl.get("name", "?"), "status": "failed", "error": str(e)})

        return deployment

    # ─── Master Pipeline ─────────────────────────────────────────────────────

    async def build_campaign(
        self,
        prompt: str,
        campaign_name: str = None,
        llm_client = None,
        foundry_client = None,
        vault_path: str = None,
        comfyui_url: str = None,
        omlx_url: str = None,
        omlx_model: str = "",
        omlx_api_key: str = None,
        on_progress: Callable = None,
    ) -> Dict[str, Any]:
        """Run the full campaign build pipeline.

        Phases:
        1. Scan FoundryVTT world
        2. Generate campaign structure via LLM
        3. Save to Obsidian vault
        4. Generate maps and portraits
        5. Deploy to FoundryVTT

        Args:
            prompt: User's campaign description
            campaign_name: Optional name for the campaign
            llm_client: httpx.AsyncClient for LLM calls
            foundry_client: Connected FoundryClient instance
            vault_path: Obsidian vault path
            comfyui_url: ComfyUI URL for map generation
            omlx_url: oMLX API URL for map generation
            omlx_model: oMLX model name
            omlx_api_key: oMLX API key (uses LLM_API_KEY if not set)
            on_progress: Optional callback(msg, step, detail)

        Returns:
            Result dict with full campaign info, asset info, and deployment status
        """
        result = {
            "status": "building",
            "campaign_name": campaign_name,
            "steps": [],
        }

        def progress(msg: str, step: str = "", detail: str = ""):
            result["steps"].append({"message": msg, "step": step, "detail": detail})
            if on_progress:
                try:
                    on_progress(msg, step, detail)
                except Exception as e:
                    logger.debug(f"Progress callback error: {e}")

        # Determine oMLX key
        api_key = omlx_api_key or self.settings.llm_api_key

        # ── Phase 1: Scan FoundryVTT world ──
        progress("🔍 Scanning connected FoundryVTT world...", step="scan")
        scan_result = None
        if foundry_client:
            try:
                scan_result = await self.scan_foundry_world(foundry_client)
                progress(
                    f"✅ Scan complete — {len(scan_result.get('scenes', []))} scenes, "
                    f"{len(scan_result.get('actors', []))} actors, "
                    f"{len(scan_result.get('users', []))} users found",
                    step="scan",
                    detail="world_scan",
                )
                result["scan"] = scan_result
            except Exception as e:
                progress(f"⚠️ Scan incomplete: {e}", step="scan")
                scan_result = {}
        else:
            progress("ℹ️ No FoundryVTT connection — running without scan", step="scan")
            scan_result = {}

        # ── Phase 2: Generate campaign via LLM ──
        progress("🏗️ Generating campaign structure via LLM...", step="generate")
        if llm_client is None:
            import httpx
            llm_client = httpx.AsyncClient(timeout=300)

        campaign_data = None
        try:
            campaign_data = await self.generate_campaign_data(prompt, llm_client, scan_result)
            if not isinstance(campaign_data, dict) or "campaign" not in campaign_data:
                raise Exception(
                    f"LLM returned incomplete campaign structure (missing 'campaign' key). "
                    f"Keys present: {list(campaign_data.keys()) if isinstance(campaign_data, dict) else type(campaign_data).__name__}"
                )
            result["campaign_data"] = campaign_data
            campaign_name = campaign_data.get("campaign", {}).get("name", "Unnamed")
            progress(f"✅ Campaign '{campaign_name}' generated", step="generate", detail="complete")

            # ── Phase 3: Save to Obsidian vault ──
            progress("💾 Saving campaign to Obsidian vault...", step="vault")
            manifest = await self.save_to_vault(campaign_data, vault_path)
            result["manifest"] = manifest
            progress(f"✅ Campaign saved to vault", step="vault", detail=manifest.get("campaign_folder", ""))

            # ── Phase 4: Generate assets (maps, portraits) ──
            progress("🎨 Generating maps and portraits...", step="assets")
            # Sanitize campaign name to prevent path traversal attacks
            safe_campaign_name = sanitize_filename(campaign_name.lower())
            asset_output_dir = Path("./campaign_assets") / (safe_campaign_name + "_maps")

            map_generator = None
            try:
                from campaign.map_generator import MapGenerator
                map_generator = MapGenerator(
                    comfyui_url=comfyui_url or getattr(settings, "comfyui_url", "http://127.0.0.1:18188"),
                    omlx_base_url=getattr(settings, "omlx_base_url", "http://localhost:8800"),
                    omlx_api_key=api_key,
                    provider="auto",
                )
            except Exception as e:
                progress(f"⚠️ Map generator init failed: {e}", step="assets")

            asset_info = {"maps": [], "portraits": [], "status": "skipped"}
            if map_generator:
                try:
                    asset_info = await self.generate_assets(campaign_data, map_generator, asset_output_dir)
                    progress(
                        f"✅ Generated {asset_info['total_maps']} map(s) and {asset_info['total_portraits']} portrait(s)",
                        step="assets",
                        detail=f"maps={asset_info['total_maps']}, portraits={asset_info['total_portraits']}",
                    )
                except Exception as e:
                    progress(f"⚠️ Asset generation failed: {e}", step="assets")
                    result["asset_error"] = str(e)
                await map_generator.close()

            result["assets"] = asset_info

            # ── Phase 5: Deploy to FoundryVTT ──
            progress("🚀 Deploying campaign to FoundryVTT...", step="deploy")
            deployment = None
            if foundry_client:
                try:
                    deployment = await self.deploy_to_foundry(campaign_data, foundry_client, asset_info, scan_result=scan_result)
                    total_deployed = sum(
                        len(deployment.get(k, []))
                        for k in ("scenes", "npcs", "journal_entries", "quest_logs", "loot_tables", "loot_piles", "playlists", "calendar_events")
                    )
                    progress(
                        f"✅ Deployed {total_deployed} elements to FoundryVTT",
                        step="deploy",
                        detail=(
                            f"scenes={len(deployment.get('scenes', []))}, "
                            f"npcs={len(deployment.get('npcs', []))}, "
                            f"journal={len(deployment.get('journal_entries', []))}, "
                            f"quests={len(deployment.get('quest_logs', []))}, "
                            f"loot_tables={len(deployment.get('loot_tables', []))}, "
                            f"loot_piles={len(deployment.get('loot_piles', []))}, "
                            f"playlists={len(deployment.get('playlists', []))}, "
                            f"calendar_events={len(deployment.get('calendar_events', []))}"
                        ),
                    )
                except Exception as e:
                    progress(f"⚠️ Deployment failed: {e}", step="deploy")
                    result["deploy_error"] = str(e)

            result["deployment"] = deployment
            result["status"] = "complete"
            result["campaign_ready"] = True
            result["ready_to_start"] = True

        except Exception as e:
            if campaign_data is None:
                result["status"] = "error"
                result["error"] = f"Campaign generation failed: {e}"
                logger.exception("Campaign generation failed")
            else:
                logger.exception("Pipeline error after campaign generation")
                if "error" not in result:
                    result["error"] = f"Pipeline error: {e}"
            return result

        finally:
            if llm_client:
                await llm_client.aclose()

        return result

    # ─── Convenience wrapper ─────────────────────────────────────────────────

    async def build_campaign_convenience(
        self,
        prompt: str,
        campaign_name: str = None,
        foundry_client = None,
        on_progress: Callable = None,
    ) -> Dict[str, Any]:
        """Simplified builder using app-level settings."""
        import httpx

        llm_client = httpx.AsyncClient(timeout=300)

        return await self.build_campaign(
            prompt=prompt,
            campaign_name=campaign_name,
            llm_client=llm_client,
            foundry_client=foundry_client,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=on_progress,
        )
