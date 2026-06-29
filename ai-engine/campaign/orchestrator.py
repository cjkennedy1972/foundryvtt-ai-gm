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
from urllib.parse import unquote

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
        level_range: str = "1-5",
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

        prompt_text = generate_campaign_prompt(
            prompt, active_modules=active_modules, level_range=level_range
        ) + scan_info

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
        """Build a rich map prompt from scene data when map_style is not provided.

        When the scene has scene_setup with walls/doors, extracts structural
        layout information (wall positions, door locations, room count)
        and includes it in the prompt so the generated map respects the
        physical layout even in text-only fallback mode.
        """
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

        # Add structural layout information from scene_setup (walls, doors)
        setup = scene.get("scene_setup", {})
        walls = setup.get("walls", [])
        doors = setup.get("doors", [])
        if walls or doors:
            wall_count = len(walls)
            door_count = len(doors)
            grid_w = setup.get("grid_width", 16)
            grid_h = setup.get("grid_height", 12)

            # Describe structural features
            layout_parts = []
            layout_parts.append(f"room layout with {wall_count} wall segments")

            # Classify wall types
            horizontal = [w for w in walls if w[1] == w[3]]  # horizontal segments
            vertical = [w for w in walls if w[0] == w[2]]  # vertical segments
            if horizontal:
                layout_parts.append(f"{len(horizontal)} horizontal walls")
            if vertical:
                layout_parts.append(f"{len(vertical)} vertical walls")

            # Classify doors
            for door in doors:
                door_type = door.get("door", 0)
                door_info = {0: "wall", 1: "open doorway", 2: "secret door"}.get(door_type, "door")
                layout_parts.append(door_info)

            layout_parts.append(f"grid {grid_w}x{grid_h}")
            layout = ", ".join(layout_parts)
            prompt_parts.insert(1, f"floorplan: {layout}")  # Insert after perspective

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
                # Derive image dimensions from the scene's grid layout so that
                # wall coordinates (placed at GRID_PX per square) align with
                # what's visible in the generated image.
                setup = scene.get("scene_setup", {})
                gw = setup.get("grid_width", 16)
                gh = setup.get("grid_height", 12)
                gp = setup.get("grid_size_px", self.GRID_PX)
                img_w = gw * gp
                img_h = gh * gp
                # Store resolved image dimensions on the scene so deploy can use them
                scene["_map_width_px"] = img_w
                scene["_map_height_px"] = img_h
                scene["_grid_size_px"] = gp

                # ── Layout-guided generation (when scene has wall/door data) ──
                walls = setup.get("walls", [])
                doors = setup.get("doors", [])
                if walls or doors:
                    logger.info(f"[Layout] Scene '{scene['name']}' has wall/door data — using ControlNet layout-guided generation")
                    # Set _output_dir so generate_layout_mask can save to the right place
                    scene["_output_dir"] = str(output_dir)
                    try:
                        layout_mask = await map_generator.generate_layout_mask(
                            scene_setup=setup,
                            width=img_w,
                            height=img_h,
                            grid_size_px=gp,
                        )
                        if layout_mask and layout_mask.exists():
                            # Use layout-guided map generation (ControlNet)
                            # Derive map style from scene type for appropriate style prefix
                            scene_type = scene.get("type", "dungeon")
                            style_map = {
                                "dungeon": "dungeon",
                                "settlement": "fantasy_map",
                                "tavern": "dungeon",
                                "cave": "dungeon",
                                "temple": "dungeon",
                                "castle": "dungeon",
                                "crypt": "dungeon",
                                "ruins": "dungeon",
                                "village": "overworld",
                                "city": "overworld",
                                "wilderness": "overworld",
                            }
                            style = style_map.get(scene_type, "dungeon")
                            map_result = await map_generator.generate_map_controlnet(
                                prompt=prompt,
                                layout_image_path=str(layout_mask),
                                output_dir=output_dir,
                                width=img_w,
                                height=img_h,
                                style=style,
                            )
                        else:
                            # Fallback: no layout possible, use text-only generation
                            logger.info(f"[Layout] No layout data to mask — falling back to text-only generation")
                            map_result = await map_generator.generate_map(
                                prompt=prompt,
                                output_dir=output_dir,
                                width=img_w,
                                height=img_h,
                            )
                    except Exception as e:
                        logger.warning(f"[Layout] Layout-guided generation failed for {scene['name']}: {e} — falling back to text-only")
                        try:
                            map_result = await map_generator.generate_map(
                                prompt=prompt,
                                output_dir=output_dir,
                                width=img_w,
                                height=img_h,
                            )
                        except Exception as fallback_e:
                            logger.warning(f"[Layout] Text-only fallback also failed for {scene['name']}: {fallback_e}")
                            map_result = {"status": "error", "error": str(fallback_e), "provider": "none"}
                else:
                    # No wall/door data — use text-only generation (existing behavior)
                    try:
                        map_result = await map_generator.generate_map(
                            prompt=prompt,
                            output_dir=output_dir,
                            width=img_w,
                            height=img_h,
                        )
                    except Exception as e:
                        logger.warning(f"Map generation error for {scene['name']}: {e}")
                        map_result = {"status": "error", "error": str(e), "provider": "none"}

                if map_result["status"] == "success":
                    results["maps"].append({
                        "scene": scene["name"],
                        "type": "scene_map",
                        "file": map_result["output_file"],
                        "provider": map_result.get("provider", "unknown"),
                    })
                    scene["map_file"] = Path(map_result["output_file"]).name
                    logger.info(f"[Layout] '{scene['name']}' map {'layout-guided' if (walls or doors) else 'text-only'} — {map_result.get('provider', 'unknown')}")
                else:
                    logger.warning(f"Map generation failed for {scene['name']}: {map_result.get('error', 'unknown')}")

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
        # portrait_needed=None means "not explicitly set" — treat as True so campaigns
        # built with partial errors still get portraits on regenerate.
        npcs = campaign_data.get("npcs", [])
        portrait_npcs = [n for n in npcs if n.get("portrait_needed") is not False]

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

    # ─── Upload generated maps and set scene backgrounds ────────────────────

    async def upload_maps_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_output_dir: Path,
        safe_name: str,
    ) -> Dict[str, Any]:
        """Upload generated map PNGs to Foundry and set background_src on each scene dict.

        Must be called AFTER generate_assets() (which populates scene["map_file"]) and
        BEFORE deploy_to_foundry() (which reads scene["background_src"] when creating scenes).

        Returns summary: {uploaded: int, failed: int, errors: list}
        """
        summary: Dict[str, Any] = {"uploaded": 0, "failed": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — map upload skipped")
            return summary

        scenes = campaign_data.get("scenes", [])
        if not scenes:
            return summary

        semaphore = asyncio.Semaphore(4)

        async def _upload_one(scene: dict):
            map_file = scene.get("map_file")
            if not map_file:
                return
            img_path = asset_output_dir / map_file
            if not img_path.exists():
                logger.warning(f"[Upload] Map file not found: {img_path}")
                summary["failed"] += 1
                summary["errors"].append(f"{scene.get('name', '?')}: file not found ({img_path.name})")
                return
            async with semaphore:
                try:
                    img_bytes = await asyncio.to_thread(img_path.read_bytes)
                    upload = await foundry_client.upload_file(
                        file_bytes=img_bytes,
                        path=f"ai-gm-maps/{safe_name}",
                        filename=map_file,
                        mime_type="image/png",
                    )
                    src = (
                        (unquote(upload.get("path")) if isinstance(upload, dict) else None)
                        or f"ai-gm-maps/{safe_name}/{map_file}"
                    )
                    scene["background_src"] = src
                    summary["uploaded"] += 1
                    logger.info(f"[Upload] '{scene.get('name', '?')}' → {src}")
                except Exception as e:
                    summary["failed"] += 1
                    msg = f"{scene.get('name', '?')}: {type(e).__name__}: {e}"
                    summary["errors"].append(msg)
                    logger.warning(f"[Upload] Map upload failed: {msg}")

        await asyncio.gather(*(_upload_one(s) for s in scenes))
        logger.info(
            f"[Upload] Map upload complete: {summary['uploaded']} uploaded, "
            f"{summary['failed']} failed"
        )
        return summary

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
            "portraits_attached": 0,
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

        # ── Load deployment state (NPC UUIDs from last deployment) ──
        campaign_assets_dir = Path("./campaign_assets") / sanitize_filename(campaign_name.lower())
        deployment_state_file = campaign_assets_dir / "deployment_state.json"
        deployment_state = {}
        npc_uuid_map = {}  # name -> uuid
        if deployment_state_file.exists():
            try:
                raw_deployment = await asyncio.to_thread(deployment_state_file.read_text, encoding="utf-8")
                deployment_state = json.loads(raw_deployment)
                for npc_info in deployment_state.get("npcs", []):
                    if npc_info.get("status") == "created" and npc_info.get("uuid"):
                        npc_uuid_map[npc_info["name"]] = npc_info["uuid"]
                logger.info(f"Loaded deployment state with {len(npc_uuid_map)} NPC UUIDs")
            except Exception as e:
                logger.warning(f"Failed to load deployment state: {e}")

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
                async def _upload_and_attach_map(scene):
                    """Upload map and attach to scene with bounded concurrency."""
                    map_file = scene.get("map_file")
                    if not map_file:
                        return
                    img_path = asset_output_dir / map_file
                    if not img_path.exists():
                        return
                    try:
                        img_bytes = await asyncio.to_thread(img_path.read_bytes)
                        upload = await foundry_client.upload_file(
                            file_bytes=img_bytes,
                            path=f"ai-gm-maps/{safe_name}",
                            filename=map_file,
                            mime_type="image/png",
                        )
                        # Prefer the path the relay reports; fall back to a constructed one.
                        # URL-decode the path (relay may return percent-encoded paths)
                        src = (
                            (unquote(upload.get("path")) if isinstance(upload, dict) else None)
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
                                return

                            logger.info(f"Current scene data keys: {list(current_scene.keys())}")

                            # Preserve existing levels and only update the Base Level background.
                            # This prevents loss of multi-level data from modules like Perfect Vision or Levels.
                            existing_levels = current_scene.get("levels", [])
                            bg_config = {
                                "src": src,
                                "offsetX": 0,
                                "offsetY": 0,
                                "scaleX": 1.0,
                                "scaleY": 1.0,
                            }
                            if existing_levels:
                                # Find and update the Base Level, or use the first level
                                base_level_idx = next(
                                    (i for i, l in enumerate(existing_levels) if l.get("name") == "Base Level"),
                                    0
                                )
                                if base_level_idx < len(existing_levels):
                                    existing_levels[base_level_idx]["background"] = bg_config
                                levels_to_send = existing_levels
                            else:
                                # Fallback: create a single Base Level if none exists
                                levels_to_send = [{"name": "Base Level", "background": bg_config}]

                            logger.info(f"Updating scene with {len(levels_to_send)} level(s), Base Level background={src}")

                            # Send the updated levels
                            logger.info(f"Sending update-scene for '{scene['name']}'...")
                            result = await foundry_client.update_scene(
                                scene["name"],
                                {"levels": levels_to_send}
                            )
                            logger.info(f"Update-scene result: {result}")
                            if result and result.get("type") != "error":
                                summary["scenes_attached"] += 1
                            elif result and result.get("type") == "error":
                                msg = f"scene '{scene['name']}': {result.get('error')}"
                                logger.error(f"Scene attachment failed: {msg}")
                                summary["errors"].append(msg)
                            else:
                                # Handle None or falsy result (network error, relay timeout)
                                msg = f"scene '{scene['name']}': no response from Foundry (possible network timeout)"
                                logger.error(f"Scene attachment failed: {msg}")
                                summary["errors"].append(msg)
                        except Exception as e:
                            msg = f"scene '{scene['name']}': {type(e).__name__}: {e}"
                            logger.exception(f"Scene attachment failed: {msg}")
                            summary["errors"].append(msg)
                    except Exception as e:
                        msg = f"scene '{scene.get('name', '?')}': {type(e).__name__}: {e}"
                        logger.exception(f"File upload/processing failed: {msg}")
                        summary["errors"].append(msg)

                # Upload maps sequentially — parallel uploads overwhelm the relay/Foundry
                # WebSocket connection causing 408 timeouts on concurrent requests.
                scenes = campaign_data.get("scenes", [])
                for scene in scenes:
                    await _upload_and_attach_map(scene)

            # ── Upload portraits + attach to existing NPCs (by name) ──
            if attach_to_foundry and connected:
                npc_list = campaign_data.get("npcs", [])
                if npc_list:
                    _progress(f"Attaching {len(npc_list)} NPC portrait(s)...")

                    async def _upload_and_attach_portrait(npc):
                        """Upload portrait and attach to NPC with bounded concurrency."""
                        portrait_file = npc.get("portrait_file")
                        if not portrait_file:
                            return
                        portrait_path = asset_output_dir / "portraits" / portrait_file
                        if not portrait_path.exists():
                            return
                        try:
                            img_bytes = await asyncio.to_thread(portrait_path.read_bytes)
                            upload = await foundry_client.upload_file(
                                file_bytes=img_bytes,
                                path=f"ai-gm-portraits/{safe_name}",
                                filename=portrait_file,
                                mime_type="image/png",
                            )
                            logger.info(f"Portrait upload response: {json.dumps(upload, default=str)}")
                            # URL-decode the path returned by relay (e.g., "the%20age" -> "the age")
                            src = (
                                (unquote(upload.get("path")) if isinstance(upload, dict) else None)
                                or f"ai-gm-portraits/{safe_name}/{portrait_file}"
                            )
                            logger.info(f"Using portrait source: {src}")
                            npc["portrait_src"] = src
                            # Update NPC actor in Foundry with the new portrait
                            try:
                                npc_name = npc["name"]
                                logger.info(f"Updating NPC '{npc_name}' with portrait {src}...")

                                # Try using UUID from deployment state first (fastest path)
                                result = None
                                if npc_name in npc_uuid_map:
                                    npc_uuid = npc_uuid_map[npc_name]
                                    logger.info(f"Using cached UUID for '{npc_name}': {npc_uuid}")
                                    result = await foundry_client.update_entity(
                                        uuid=npc_uuid,
                                        data={"img": src}
                                    )
                                else:
                                    # Fall back to searching by name
                                    result = await foundry_client.update_actor(
                                        actor_name=npc_name,
                                        actor_data={"img": src}
                                    )

                                if result:
                                    logger.debug(f"Update response: {json.dumps(result, default=str)}")

                                if result and result.get("type") != "error":
                                    logger.info(f"Updated NPC actor: {result}")
                                    summary["portraits_attached"] += 1
                                elif result and result.get("type") == "error":
                                    logger.error(f"Failed to update portrait for NPC '{npc_name}': {result.get('error')}")
                                    summary["errors"].append(f"Portrait update failed for '{npc_name}': {result.get('error')}")
                                else:
                                    logger.info(f"NPC '{npc_name}' not deployed in Foundry yet (not an error)")
                            except KeyError as e:
                                msg = f"NPC has missing field {e}"
                                logger.exception(f"NPC update failed: {msg}")
                                summary["errors"].append(msg)
                            except Exception as e:
                                msg = f"NPC '{npc.get('name', '?')}': {type(e).__name__}: {e}"
                                logger.exception(f"NPC update failed: {msg}")
                                summary["errors"].append(msg)
                        except Exception as e:
                            msg = f"NPC '{npc.get('name', '?')}': {type(e).__name__}: {e}"
                            logger.exception(f"Portrait upload/processing failed: {msg}")
                            summary["errors"].append(msg)

                    # Upload portraits in parallel with bounded concurrency (max 4 concurrent)
                    semaphore = asyncio.Semaphore(4)
                    async def _with_semaphore(npc):
                        async with semaphore:
                            await _upload_and_attach_portrait(npc)
                    await asyncio.gather(*(_with_semaphore(n) for n in npc_list))
            elif attach_to_foundry and not connected:
                summary["errors"].append(
                    "Foundry not connected — images regenerated and saved, but not attached to scenes/NPCs"
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
            f"attached {summary['scenes_attached']} scenes and {summary.get('portraits_attached', 0)} portraits to Foundry",
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
            "encounters": [],
            "encounter_actors": [],
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
                        npc_flags["autoanimations"] = {
                            "killAnim": False,
                            "animationType": npc.get("animation_type", "melee"),
                        }

                    # Maxwell's Maladies (mmm) — condition tracking
                    if "mmm" in mods and isinstance(npc.get("conditions"), list):
                        npc_flags["mmm"] = {
                            "track_conditions": True,
                            "active_conditions": npc.get("conditions", []),
                        }

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

                    # Midi QOL — spell and combat automation flags
                    if "midi-qol" in mods:
                        midi_flags: Dict[str, Any] = {
                            "concentration-automation": npc.get("concentration_caster", False),
                            "critThreshold": npc.get("critical_threshold", 20),
                            "allowUseMacro": npc.get("use_macros", False),
                        }
                        # Support for auto-damage application
                        if npc.get("auto_damage_type"):
                            midi_flags["autoApplyDamage"] = npc.get("auto_damage_type") in ["auto", "both"]
                        # Support for advantage/disadvantage conditions
                        if npc.get("disadvantage_attacks"):
                            midi_flags["disadvantageAttacks"] = True
                        if midi_flags:
                            npc_flags["midi-qol"] = midi_flags

                    # Token-specific configuration
                    prototype_token: Dict[str, Any] = {}

                    # Token Notes — GM-only secret information
                    if "token-notes" in mods and npc.get("gm_token_note"):
                        prototype_token.setdefault("flags", {})["token-notes"] = {
                            "note": npc["gm_token_note"]
                        }

                    # Polyglot — NPC language configuration
                    if "polyglot" in mods and npc.get("language_spoken"):
                        prototype_token.setdefault("flags", {})["polyglot"] = {
                            "language": npc["language_spoken"]
                        }

                    # Patrol — guard NPCs with waypoint routes
                    if "patrol" in mods and npc.get("npc_type") == "guard":
                        patrol_config: Dict[str, Any] = {
                            "active": True,
                            "speed": npc.get("patrol_speed", 1),
                            "pause": npc.get("patrol_pause", 3000),
                        }
                        if npc.get("patrol_route"):
                            patrol_config["route"] = npc["patrol_route"]
                        prototype_token.setdefault("flags", {})["patrol"] = patrol_config

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
                        effects = []
                        for ae in npc["active_effects"]:
                            effect_data: Dict[str, Any] = {
                                "name": ae.get("name") or ae.get("label") or "Effect",
                                "icon": ae.get("icon", "icons/svg/aura.svg"),
                                "description": ae.get("description", ""),
                                "disabled": ae.get("disabled", False),
                                "transfer": ae.get("transfer", True),
                                "changes": ae.get("changes", []),
                            }
                            # Support duration if times-up module is active
                            if "times-up" in mods and ae.get("duration"):
                                effect_data["duration"] = ae["duration"]
                            effects.append(effect_data)
                        data["effects"] = effects

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
                            "type": "text",
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
                        # dnd5e only accepts a fixed set of Item types. Map common
                        # LLM-produced types to valid ones; currency isn't an Item.
                        VALID_ITEM_TYPES = {
                            "weapon", "equipment", "consumable", "tool",
                            "loot", "container", "feat", "spell", "backpack",
                        }
                        TYPE_ALIASES = {
                            "wondrous_item": "equipment", "wondrous": "equipment",
                            "ring": "equipment", "rod": "equipment", "wand": "consumable",
                            "staff": "weapon", "scroll": "consumable", "potion": "consumable",
                            "armor": "equipment", "gear": "loot", "treasure": "loot",
                            "gem": "loot", "trade_good": "loot",
                        }
                        pile_items = []
                        for e in table.get("entries", []):
                            raw_type = e.get("foundry_item_type", "loot")
                            # Currency is not an Item document — fold it into the pile, skip here.
                            if raw_type == "currency":
                                continue
                            item_type = TYPE_ALIASES.get(raw_type, raw_type)
                            if item_type not in VALID_ITEM_TYPES:
                                item_type = "loot"
                            pile_items.append({
                                "name": e.get("name", "Loot"),
                                "type": item_type,
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

                    # ── Apply module flags from structured module_flags object or fallback to scene fields ──
                    module_flags = scene.get("module_flags", {})

                    # Dynamic Soundscapes
                    if "dynamic-soundscapes" in mods:
                        soundscape_config = module_flags.get("dynamic-soundscapes")
                        if soundscape_config:
                            scene_flags["dynamic-soundscapes"] = soundscape_config
                        elif scene.get("soundscape", "none") != "none":
                            # Fallback: use top-level soundscape field
                            scene_flags["dynamic-soundscapes"] = {
                                "ambient": True,
                                "preset": scene.get("soundscape", ""),
                                "volume": scene.get("soundscape_volume", 0.6),
                            }

                    # Levels — multi-floor scenes
                    if "levels" in mods:
                        levels_config = module_flags.get("levels")
                        if levels_config:
                            scene_flags["levels"] = levels_config
                        elif scene.get("has_multiple_floors") and scene.get("floors"):
                            # Fallback: use top-level fields
                            scene_flags["levels"] = {"sceneLevels": scene["floors"]}

                    # Better Roofs
                    if "betterroofs" in mods:
                        roofs_config = module_flags.get("betterroofs")
                        if roofs_config:
                            scene_flags["betterroofs"] = roofs_config
                        elif scene.get("has_roof"):
                            # Fallback: use top-level field
                            scene_flags["betterroofs"] = {"roofEnabled": True}

                    # Fog Weaver — atmospheric fog overlays
                    if "fog-weaver" in mods:
                        fog_config = module_flags.get("fog-weaver")
                        if fog_config:
                            scene_flags["fog-weaver"] = fog_config
                        elif scene.get("fog_type", "none") != "none":
                            # Fallback: use top-level fields
                            scene_flags["fog-weaver"] = {
                                "fogType": scene.get("fog_type", "light_fog"),
                                "fogDensity": scene.get("fog_density", 0.2),
                                "enabled": True,
                            }

                    # SmallTime — in-world time-of-day display
                    if "smalltime" in mods:
                        time_config = module_flags.get("smalltime")
                        if time_config:
                            scene_flags["smalltime"] = time_config
                        elif scene.get("time_of_day") is not None:
                            # Fallback: use top-level fields
                            scene_flags["smalltime"] = {
                                "timeOfDay": scene.get("time_of_day", 12),
                                "timePeriod": scene.get("time_period", "afternoon"),
                            }

                    data = {
                        "name": scene["name"],
                        "darkness": scene.get("darkness", 0.0),
                        "flags": scene_flags,
                    }
                    # Set scene canvas dimensions from the grid layout so that
                    # walls placed during enrichment (at grid_size_px per square)
                    # align with the generated background image.
                    gp = scene.get("_grid_size_px", self.GRID_PX)
                    setup = scene.get("scene_setup", {})
                    gw = setup.get("grid_width")
                    gh = setup.get("grid_height")
                    if gw and gh:
                        data["width"] = scene.get("_map_width_px", gw * gp)
                        data["height"] = scene.get("_map_height_px", gh * gp)
                        # ponytail: set padding=0 during creation so walls align correctly; caller can adjust after walls are placed
                        data["grid"] = {"size": gp, "padding": 0}
                        data["padding"] = 0  # Also set scene-level padding to 0 (separate from grid.padding)
                    # FoundryVTT v14: Scenes use a Levels system. Create with a default level.
                    # If we have a background image reference, attach it to the level.
                    background_src = scene.get("background_src")
                    bg_config = {}
                    if background_src:
                        bg_config = {
                            "src": background_src,
                            "offsetX": 0,
                            "offsetY": 0,
                            "scaleX": 1.0,
                            "scaleY": 1.0,
                        }
                    levels = [
                        {
                            "name": "Base Level",
                            "background": bg_config,
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

        # ── Encounters ────────────────────────────────────────────────────────
        encounters = campaign_data.get("encounters", [])
        if encounters:
            logger.info(f"Deploying {len(encounters)} encounter(s)...")
            try:
                enc_results = await self.deploy_encounters(campaign_data, foundry_client, deployment, mods)
                deployment["encounters"] = enc_results
            except Exception as e:
                logger.warning(f"Encounter deployment failed: {e}")
                deployment["encounters"] = [{"status": "failed", "error": str(e)}]

        # ── Portraits for compendium-less placeholder monsters ─────────────────
        # Encounter monsters with no compendium match are flagged needs_portrait;
        # generate AI art for them (falls back to themed icons if ComfyUI is down).
        try:
            cname = campaign_data.get("campaign", {}).get("name") or "campaign"
            portrait_summary = await self._generate_placeholder_portraits(foundry_client, cname)
            deployment["placeholder_portraits"] = portrait_summary
        except Exception as e:
            logger.warning(f"Placeholder portrait pass failed: {e}")

        return deployment

    # ─── Phase 5c: Deploy pre-staged encounters ──────────────────────────────

    async def _ensure_monster_actor(
        self,
        foundry_client,
        name: str,
        cr: float = 1,
        hp: int = 10,
        ac: int = 10,
    ) -> Optional[str]:
        """Return the UUID of a world actor matching `name`, creating one if needed."""
        from campaign.monster_actor import ensure_monster_actor
        return await ensure_monster_actor(foundry_client, name, cr=cr, hp=hp, ac=ac)

    @staticmethod
    def _default_monster_icon(name: str) -> str:
        """Pick a guaranteed-present core Foundry icon for a portrait-less monster.

        Used only when ComfyUI is unreachable so a placeholder is never left
        with the blank mystery-man icon.
        """
        n = name.lower()
        undead = ("undead", "skeleton", "zombie", "wraith", "shadow", "ghost",
                  "ghoul", "lich", "specter", "spectre", "wight", "vampire")
        if any(k in n for k in undead):
            return "icons/svg/skull.svg"
        if any(k in n for k in ("fire", "flame", "demon", "devil", "fiend")):
            return "icons/svg/fire.svg"
        return "icons/svg/mystery-man.svg"

    async def _generate_placeholder_portraits(
        self,
        foundry_client,
        campaign_name: str,
        output_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Generate AI portraits for art-less placeholder monster actors.

        `ensure_monster_actor` flags placeholders created without compendium
        art (flags["ai-gm"].needs_portrait). This pass finds those actors,
        generates a ComfyUI portrait for each, uploads it, sets it as the
        actor img + token art, and clears the flag. If ComfyUI is unreachable,
        falls back to a themed core icon so the actor is never left blank.
        """
        summary: Dict[str, Any] = {"generated": 0, "fallback_icon": 0, "errors": []}
        if not foundry_client or not foundry_client.is_connected:
            return summary

        # Catch actors explicitly flagged needs_portrait, plus any legacy
        # auto_placeholder monster whose art is still blank/mystery-man (created
        # before the flag existed) so existing worlds self-heal on next deploy.
        find_js = (
            "return game.actors.filter(a => {"
            "  const f = a.flags?.['ai-gm'];"
            "  if (!f) return false;"
            "  if (f.needs_portrait) return true;"
            "  if (f.auto_placeholder && (!a.img || a.img.includes('mystery-man'))) return true;"
            "  return false;"
            "}).map(a => ({uuid: a.uuid, name: a.name}));"
        )
        try:
            res = await foundry_client.execute_js(find_js)
            pending = res.get("result") if isinstance(res, dict) else None
            pending = pending if isinstance(pending, list) else []
        except Exception as e:
            summary["errors"].append(f"lookup: {e}")
            return summary

        if not pending:
            return summary

        logger.info(f"[Placeholder Portraits] {len(pending)} monster(s) need art")

        from campaign.map_generator import MapGenerator
        map_generator = MapGenerator(
            comfyui_url=getattr(self.settings, "comfyui_url", "http://127.0.0.1:18188"),
        )
        safe_name = sanitize_filename(campaign_name.lower())
        base_dir = output_dir or (Path("./campaign_assets") / safe_name)
        portraits_dir = base_dir / "portraits"
        portraits_dir.mkdir(parents=True, exist_ok=True)

        try:
            comfy_up = (await map_generator.health_check()).get("comfyui")
            if not comfy_up:
                logger.warning("[Placeholder Portraits] ComfyUI unreachable — using fallback icons")
            for actor in pending:
                name = actor.get("name", "Monster")
                actor_uuid = actor.get("uuid", "")
                if not actor_uuid:
                    continue
                src = None
                if comfy_up:
                    try:
                        prompt = (
                            f"fantasy TTRPG monster portrait of a {name}, "
                            f"head and shoulders, detailed, dramatic lighting, painterly"
                        )
                        pres = await map_generator.generate_portrait(prompt, portraits_dir)
                        if pres.get("status") == "success":
                            pfile = Path(pres["output_file"])
                            img_bytes = await asyncio.to_thread(pfile.read_bytes)
                            upload = await foundry_client.upload_file(
                                file_bytes=img_bytes,
                                path=f"ai-gm-portraits/{safe_name}",
                                filename=pfile.name,
                                mime_type="image/png",
                            )
                            src = (
                                (unquote(upload.get("path")) if isinstance(upload, dict) else None)
                                or f"ai-gm-portraits/{safe_name}/{pfile.name}"
                            )
                    except Exception as e:
                        summary["errors"].append(f"{name}: {e}")

                if src:
                    summary["generated"] += 1
                else:
                    src = self._default_monster_icon(name)
                    summary["fallback_icon"] += 1

                try:
                    await foundry_client.update_entity(
                        uuid=actor_uuid,
                        data={
                            "img": src,
                            "prototypeToken": {"texture": {"src": src}},
                            "flags": {"ai-gm": {"needs_portrait": False}},
                        },
                    )
                except Exception as e:
                    summary["errors"].append(f"{name} update: {e}")
        finally:
            await map_generator.close()

        logger.info(
            f"[Placeholder Portraits] generated={summary['generated']} "
            f"icon_fallback={summary['fallback_icon']} errors={len(summary['errors'])}"
        )
        return summary

    def _wall_blocked_squares(self, scene_setup: dict) -> set:
        """Return a set of (grid_x, grid_y) squares that are fully interior to a wall segment.

        Wall segments are line segments — we mark both endpoint squares as
        "avoid" rather than computing full polygon intersection, which is
        sufficient to prevent tokens spawning directly inside thick walls.
        """
        blocked = set()
        for seg in scene_setup.get("walls", []):
            if len(seg) != 4:
                continue
            x0, y0, x1, y1 = seg
            # Mark endpoint squares
            blocked.add((int(x0), int(y0)))
            blocked.add((int(x1), int(y1)))
            # Mark squares along axis-aligned segments
            if x0 == x1:
                for y in range(int(min(y0, y1)), int(max(y0, y1)) + 1):
                    blocked.add((int(x0), y))
            elif y0 == y1:
                for x in range(int(min(x0, x1)), int(max(x0, x1)) + 1):
                    blocked.add((x, int(y0)))
        return blocked

    def _safe_fallback_positions(
        self,
        scene_setup: dict,
        blocked: set,
        count: int,
        start_offset: int = 0,
    ) -> list:
        """Return `count` open grid positions spread across the scene, skipping wall-blocked squares."""
        gw = scene_setup.get("grid_width", 16)
        gh = scene_setup.get("grid_height", 12)
        candidates = [
            (x, y)
            for x in range(1, gw - 1)
            for y in range(1, gh - 1)
            if (x, y) not in blocked
        ]
        # Evenly space picks across the candidate list
        step = max(1, len(candidates) // max(count, 1))
        return [candidates[(start_offset + i * step) % len(candidates)] for i in range(count)]

    async def deploy_encounters(
        self,
        campaign_data: dict,
        foundry_client,
        deployment: dict,
        mods: dict,
    ) -> list:
        """Phase 5c — place pre-staged encounter tokens on their linked scenes.

        For each encounter:
        - Switches to the linked scene (which has walls and map image from enrichment).
        - Finds or imports each monster actor from the compendium.
        - Places hidden tokens at the LLM-specified grid positions, falling back to
          open (non-wall-blocked) squares when placement coordinates are missing or unsafe.
        - Creates a GM-only JournalEntry "Encounter: <name>" with difficulty badge,
          trigger, environment notes, and tactical tips.

        Tokens are placed hidden=True so the GM reveals them when the encounter begins.
        Returns a list of per-encounter result dicts.
        """
        results: List[Dict[str, Any]] = []
        encounters = campaign_data.get("encounters", [])
        if not encounters:
            return results

        gs = self.GRID_PX  # pixels per grid square

        # Index scenes for fast wall/grid lookup
        scene_index: Dict[str, dict] = {s["name"]: s for s in campaign_data.get("scenes", [])}
        deployed_scene_names = {
            s["name"] for s in deployment.get("scenes", []) if s.get("status") == "created"
        }

        for enc in encounters:
            enc_name = enc.get("name", "Unnamed Encounter")
            linked_scene = enc.get("linked_scene", "")

            # Fuzzy-match: if the LLM produced a scene name that doesn't exactly
            # match a deployed scene, try a case-insensitive substring match so
            # minor hallucinations (extra words, em-dash variants) still resolve.
            if linked_scene and linked_scene not in deployed_scene_names:
                linked_lower = linked_scene.lower()
                matched = None
                # Level 1: substring match (catches extra words / em-dash variants)
                for candidate in deployed_scene_names:
                    if linked_lower in candidate.lower() or candidate.lower() in linked_lower:
                        matched = candidate
                        break
                # Level 2: word-overlap score (catches total hallucinations)
                if not matched:
                    stop_words = {"the", "a", "an", "of", "in", "at", "on", "and", "or", "to"}
                    query_words = {w for w in linked_lower.split() if w not in stop_words and len(w) > 2}
                    best_score, best_candidate = 0, None
                    for candidate in deployed_scene_names:
                        cand_words = {w for w in candidate.lower().split() if w not in stop_words and len(w) > 2}
                        if not query_words or not cand_words:
                            continue
                        overlap = len(query_words & cand_words)
                        score = overlap / max(len(query_words), len(cand_words))
                        if score > best_score:
                            best_score, best_candidate = score, candidate
                    if best_score >= 0.25:
                        matched = best_candidate
                if matched:
                    logger.warning(
                        f"[Encounter] '{enc_name}': linked_scene '{linked_scene}' "
                        f"not found — fuzzy-matched to '{matched}'"
                    )
                    linked_scene = matched
                else:
                    logger.warning(
                        f"[Encounter] '{enc_name}': linked_scene '{linked_scene}' "
                        f"not found and no fuzzy match among {deployed_scene_names}"
                    )

            enc_result: Dict[str, Any] = {
                "name": enc_name,
                "scene": linked_scene,
                "tokens_placed": 0,
                "journal_created": False,
                "status": "ok",
                "errors": [],
            }

            # ── Token placement (only if scene was deployed) ──────────────────
            if linked_scene and linked_scene in deployed_scene_names:
                try:
                    await foundry_client.set_active_scene(linked_scene)
                    hook_fired = await foundry_client.wait_for_hook("renderCanvasFrame", timeout=5) or \
                                await foundry_client.wait_for_hook("sceneActivated", timeout=2)
                    if not hook_fired:
                        await asyncio.sleep(0.5)
                except Exception as e:
                    enc_result["errors"].append(f"scene switch: {e}")
                    enc_result["status"] = "partial"

                scene_data = scene_index.get(linked_scene, {})
                scene_setup = scene_data.get("scene_setup", {})
                blocked = self._wall_blocked_squares(scene_setup)

                token_offset = 0  # stagger fallback positions across monster groups
                for monster_group in enc.get("monsters", []):
                    monster_name = monster_group.get("name", "Unknown")
                    compendium_search = monster_group.get("compendium_search", monster_name)
                    count = monster_group.get("count", 1)
                    disposition = monster_group.get("disposition", -1)
                    cr = monster_group.get("cr", 1)
                    hp = monster_group.get("hp", max(1, int(cr) * 7 + 3))
                    ac = monster_group.get("ac", 10 + min(int(cr), 5))
                    placements = monster_group.get("placement", [])

                    # Resolve fallback positions for tokens with no explicit placement
                    fallback_positions = self._safe_fallback_positions(
                        scene_setup, blocked, count, start_offset=token_offset
                    )
                    token_offset += count

                    # Ensure actor exists in world
                    actor_uuid = await self._ensure_monster_actor(
                        foundry_client, compendium_search, cr=cr, hp=hp, ac=ac
                    )
                    actor_id = actor_uuid.split(".")[-1] if actor_uuid else None

                    # Track the actor UUID so teardown can delete it. Covers the
                    # cases the ai-gm flag misses: actors reused from a prior
                    # deploy and compendium imports created before flagging.
                    if actor_uuid:
                        enc_actors = deployment.setdefault("encounter_actors", [])
                        if not any(a.get("uuid") == actor_uuid for a in enc_actors):
                            enc_actors.append({"name": monster_name, "uuid": actor_uuid})

                    for i in range(count):
                        # Resolve grid position: explicit placement → fallback
                        if i < len(placements):
                            gx = placements[i].get("grid_x", fallback_positions[i][0])
                            gy = placements[i].get("grid_y", fallback_positions[i][1])
                            # Nudge off a wall-blocked square
                            if (gx, gy) in blocked and i < len(fallback_positions):
                                gx, gy = fallback_positions[i]
                        else:
                            gx, gy = fallback_positions[i]

                        # Convert grid square → pixel (top-left of square)
                        x_px = int(gx * gs)
                        y_px = int(gy * gs)

                        label = f"{monster_name} {i + 1}" if count > 1 else monster_name
                        token_data: Dict[str, Any] = {
                            "name": label,
                            "x": x_px,
                            "y": y_px,
                            "hidden": True,
                            "disposition": disposition,
                            "width": 1,
                            "height": 1,
                        }
                        if actor_id:
                            token_data["actorId"] = actor_id
                            token_data["actorLink"] = False

                        try:
                            await foundry_client.canvas_create("tokens", token_data)
                            enc_result["tokens_placed"] += 1
                            logger.info(
                                f"[Encounter] Placed '{label}' at grid ({gx},{gy}) "
                                f"= pixel ({x_px},{y_px}) on '{linked_scene}'"
                            )
                        except Exception as e:
                            enc_result["errors"].append(f"token '{label}': {e}")
                            enc_result["status"] = "partial"
            else:
                reason = "not deployed" if linked_scene else "no linked_scene"
                enc_result["errors"].append(f"token placement skipped ({reason})")
                enc_result["status"] = "partial"

            # ── GM-only encounter brief journal entry ─────────────────────────
            try:
                difficulty_color = {
                    "easy": "#2ecc71", "medium": "#f39c12",
                    "hard": "#e74c3c", "deadly": "#8e44ad",
                }.get(enc.get("difficulty", "medium"), "#e67e22")

                monster_rows = "".join(
                    f"<tr><td><strong>{m['name']}</strong></td>"
                    f"<td>×{m.get('count', 1)}</td>"
                    f"<td>CR {m.get('cr', '?')}</td>"
                    f"<td>HP {m.get('hp', '?')} / AC {m.get('ac', '?')}</td></tr>"
                    for m in enc.get("monsters", [])
                )
                reward_items = "".join(
                    f"<li>{r}</li>" for r in enc.get("rewards", [])
                )
                body = (
                    f'<h2 style="border-left:4px solid {difficulty_color};padding-left:8px">'
                    f'Encounter — {enc_name}</h2>'
                    f'<p><strong>Scene:</strong> {linked_scene}<br>'
                    f'<strong>Act:</strong> {enc.get("act", "?")}<br>'
                    f'<strong>Difficulty:</strong> '
                    f'<span style="color:{difficulty_color};font-weight:bold">'
                    f'{enc.get("difficulty", "medium").upper()}</span><br>'
                    f'<strong>XP Award:</strong> {enc.get("xp_award", 0)} XP</p>'
                    f'<p><em><strong>Trigger:</strong> {enc.get("trigger", "")}</em></p>'
                    f'<h3>Description</h3><p>{enc.get("description", "")}</p>'
                    f'<h3>Monsters</h3>'
                    f'<table><thead><tr><th>Name</th><th>Count</th><th>CR</th><th>Stats</th></tr></thead>'
                    f'<tbody>{monster_rows}</tbody></table>'
                    f'<h3>Environment &amp; Cover</h3><p>{enc.get("environment_notes", "")}</p>'
                    f'<h3>Tactical Notes (GM Only)</h3><p>{enc.get("tactical_notes", "")}</p>'
                    f'<h3>Rewards</h3><ul>{reward_items}</ul>'
                    f'<p><em>Tokens are pre-staged hidden on the scene. '
                    f'Reveal them when the encounter triggers.</em></p>'
                )
                journal_flags: Dict[str, Any] = {
                    "ai-gm": {
                        "type": "encounter_brief",
                        "act": enc.get("act", 1),
                        "linked_scene": linked_scene,
                        "difficulty": enc.get("difficulty", "medium"),
                    }
                }

                # ── Module-specific encounter configuration ──────────────────
                if "combatbooster" in mods:
                    difficulty = enc.get("difficulty", "medium")
                    journal_flags["combatbooster"] = {
                        "encounterNote": True,
                        "difficulty": difficulty,
                        "xp_reward": enc.get("xp_award", 0),
                        "show_encounter_status": True,
                    }

                if "midi-qol" in mods:
                    journal_flags["midi-qol"] = {
                        "use_midi_rolls": True,
                        "auto_apply_damage": enc.get("midi_qol", {}).get("auto_damage", True),
                        "concentration_penalty": True,
                    }

                if "autoanimations" in mods:
                    journal_flags["autoanimations"] = {
                        "enable_spell_animations": True,
                        "enable_melee_animations": True,
                    }

                if "dae" in mods:
                    journal_flags["dae"] = {
                        "enable_active_effects": True,
                        "track_conditions": True,
                    }
                journal_data = {
                    "name": f"[Encounter] {enc_name}",
                    "pages": [
                        {
                            "name": enc_name,
                            "type": "text",
                            "text": {"content": body, "format": 1},
                        }
                    ],
                    "flags": journal_flags,
                }
                je_result = await foundry_client._send(
                    "create", entityType="JournalEntry", data=journal_data
                )
                je_uuid = (je_result.get("data", {}) or {}).get("uuid", "")
                enc_result["journal_uuid"] = je_uuid
                enc_result["journal_created"] = True
            except Exception as e:
                enc_result["errors"].append(f"journal: {e}")
                enc_result["status"] = "partial"

            results.append(enc_result)
            logger.info(
                f"[Encounter] '{enc_name}': {enc_result['tokens_placed']} tokens placed, "
                f"journal={enc_result['journal_created']}, status={enc_result['status']}"
            )

        return results

    # ─── Phase 5b: Enrich deployed scenes with walls/lights/sounds ──────────

    # Pixels per grid square for all generated scenes.
    # 64px/sq means: 16×12 grid → 1024×768px, 20×15 → 1280×960px, 24×18 → 1536×1152px.
    # All clean multiples — image dimensions, scene canvas, and wall coords stay in sync.
    GRID_PX: int = 64

    def _scene_setup_to_canvas(
        self,
        setup: dict,
        grid_size: int = None,
    ) -> dict:
        """Convert a scene_setup block (grid-square coordinates) to Foundry canvas data.

        Returns a dict with keys: walls, lights, sounds, scene_config.
        All coordinates are converted from grid squares to pixels using grid_size
        (defaults to GRID_PX = 64).
        """
        gs = grid_size if grid_size is not None else self.GRID_PX

        # --- Walls ---
        foundry_walls = []
        for seg in setup.get("walls", []):
            if len(seg) == 4:
                x0, y0, x1, y1 = [v * gs for v in seg]
                foundry_walls.append({"c": [x0, y0, x1, y1], "move": 20, "sense": 20, "sound": 20, "door": 0, "ds": 0})

        # --- Doors (override or supplement wall segments) ---
        for door in setup.get("doors", []):
            c_raw = door.get("c", [])
            if len(c_raw) == 4:
                x0, y0, x1, y1 = [v * gs for v in c_raw]
                foundry_walls.append({
                    "c": [x0, y0, x1, y1],
                    "move": 20,
                    "sense": 20,
                    "sound": 20,
                    "door": door.get("door", 1),
                    "ds": door.get("ds", 0),
                })

        # --- Lights ---
        foundry_lights = []
        for light in setup.get("lights", []):
            x_px = light.get("x", 0) * gs
            y_px = light.get("y", 0) * gs
            foundry_lights.append({
                "x": x_px,
                "y": y_px,
                "config": {
                    "bright": light.get("bright", 20),
                    "dim": light.get("dim", 40),
                    "color": light.get("color", "#ff6600"),
                    "alpha": light.get("alpha", 0.5),
                    "angle": 360,
                },
            })

        # --- Sounds ---
        # Foundry AmbientSound.radius is in scene distance units (feet in D&D 5e),
        # NOT pixels. 1 grid square = 5 feet, so convert grid-square radius → feet.
        foundry_sounds = []
        for sound in setup.get("sounds", []):
            x_px = sound.get("x", 0) * gs
            y_px = sound.get("y", 0) * gs
            radius_sq = sound.get("radius", 15)
            radius_ft = radius_sq * 5  # grid squares → feet
            foundry_sounds.append({
                "x": x_px,
                "y": y_px,
                "path": sound.get("path", ""),
                "radius": radius_ft,
                "volume": sound.get("volume", 0.5),
                "repeat": True,
            })

        # --- Scene config (lighting/fog only — dimensions set at creation time) ---
        scene_config = {}
        if "darkness" in setup:
            scene_config["darkness"] = setup["darkness"]
        if "global_illumination" in setup:
            scene_config["globalLight"] = setup["global_illumination"]
        if "fog_exploration" in setup:
            scene_config["fogExploration"] = setup["fog_exploration"]
        if "token_vision" in setup:
            scene_config["tokenVision"] = setup["token_vision"]

        return {
            "walls": foundry_walls,
            "lights": foundry_lights,
            "sounds": foundry_sounds,
            "scene_config": scene_config,
        }

    async def enrich_scenes(
        self,
        campaign_data: dict,
        foundry_client,
        deployment: dict,
        on_progress: Optional[Callable] = None,
    ) -> dict:
        """Phase 5b — populate deployed scenes with walls, lights, sounds, and scene config.

        For each deployed scene that has a `scene_setup` block in campaign_data,
        switches to that scene and places all canvas elements. Falls back gracefully
        if a scene isn't found or the relay times out.

        Returns a summary dict: {enriched: int, skipped: int, errors: list}
        """
        summary = {"enriched": 0, "skipped": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — scene enrichment skipped")
            return summary

        # Build a fast name→uuid lookup from the deployment result
        deployed_scene_names = {
            s["name"] for s in deployment.get("scenes", []) if s.get("status") == "created"
        }

        for scene in campaign_data.get("scenes", []):
            scene_name = scene.get("name", "")
            setup = scene.get("scene_setup")
            if not setup:
                summary["skipped"] += 1
                logger.info(f"[Enrich] '{scene_name}' has no scene_setup — skipping")
                continue

            if scene_name not in deployed_scene_names:
                summary["skipped"] += 1
                logger.info(f"[Enrich] '{scene_name}' was not deployed — skipping")
                continue

            if on_progress:
                try:
                    on_progress(f"🏗️ Enriching scene: {scene_name}", step="enrich")
                except Exception:
                    pass

            # Use per-scene grid_size_px if the LLM specified one, else global GRID_PX
            grid_size = setup.get("grid_size_px", self.GRID_PX)
            canvas_data = self._scene_setup_to_canvas(setup, grid_size=grid_size)
            walls = canvas_data["walls"]
            lights = canvas_data["lights"]
            sounds = canvas_data["sounds"]
            scene_config = canvas_data["scene_config"]

            errors_this_scene = []

            # Switch to the scene
            try:
                await foundry_client.set_active_scene(scene_name)
                hook_fired = await foundry_client.wait_for_hook("renderCanvasFrame", timeout=5) or \
                            await foundry_client.wait_for_hook("sceneActivated", timeout=2)
                if not hook_fired:
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"[Enrich] Could not switch to '{scene_name}': {e}")
                errors_this_scene.append(f"scene switch: {e}")

            # Apply scene config (darkness, fog, vision)
            if scene_config:
                try:
                    await foundry_client.configure_scene(scene_config)
                    logger.info(f"[Enrich] '{scene_name}': configured {list(scene_config.keys())}")
                except Exception as e:
                    logger.warning(f"[Enrich] Scene config failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"scene config: {e}")

            # Place walls
            if walls:
                try:
                    await foundry_client.canvas_create("walls", walls)
                    logger.info(f"[Enrich] '{scene_name}': placed {len(walls)} walls")
                    # After walls are placed, reset padding to a comfortable value for display
                    try:
                        await foundry_client.update_scene(scene_name, {"grid": {"padding": 0.1}, "padding": 0.1})
                    except Exception as e:
                        logger.warning(f"[Enrich] Failed to set padding for '{scene_name}': {e}")
                except Exception as e:
                    logger.warning(f"[Enrich] Wall placement failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"walls: {e}")

            # Place lights
            if lights:
                try:
                    await foundry_client.canvas_create("lights", lights)
                    logger.info(f"[Enrich] '{scene_name}': placed {len(lights)} lights")
                except Exception as e:
                    logger.warning(f"[Enrich] Light placement failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"lights: {e}")

            # Place sounds
            if sounds:
                try:
                    await foundry_client.canvas_create("sounds", sounds)
                    logger.info(f"[Enrich] '{scene_name}': placed {len(sounds)} sounds")
                except Exception as e:
                    logger.warning(f"[Enrich] Sound placement failed for '{scene_name}': {e}")
                    errors_this_scene.append(f"sounds: {e}")

            if errors_this_scene:
                summary["errors"].extend([f"'{scene_name}': {e}" for e in errors_this_scene])
                # Partial enrichment still counts
                summary["enriched"] += 1
            else:
                summary["enriched"] += 1
                logger.info(f"[Enrich] '{scene_name}' fully enriched")

        return summary

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
        level_range: str = "1-5",
    ) -> Dict[str, Any]:
        """Run the full campaign build pipeline.

        Phases:
        1. Scan FoundryVTT world
        2. Generate campaign structure via LLM
        3. Save to Obsidian vault
        4. Generate maps and portraits
        5. Deploy to FoundryVTT
        5b. Enrich scenes — place walls, lights, sounds, configure fog/darkness

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
            campaign_data = await self.generate_campaign_data(prompt, llm_client, scan_result, level_range=level_range)
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
            campaign_assets_dir = Path("./campaign_assets") / safe_campaign_name
            # Ensure campaign assets directory exists for storing deployment state
            await asyncio.to_thread(campaign_assets_dir.mkdir, parents=True, exist_ok=True)

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

            # ── Phase 4b: Upload maps to Foundry and set scene backgrounds ──
            if foundry_client and asset_info.get("total_maps", 0) > 0:
                progress("📤 Uploading maps to FoundryVTT...", step="upload")
                try:
                    upload_summary = await self.upload_maps_to_foundry(
                        campaign_data,
                        foundry_client,
                        asset_output_dir,
                        safe_campaign_name,
                    )
                    progress(
                        f"✅ Uploaded {upload_summary['uploaded']} map(s) to Foundry",
                        step="upload",
                        detail=f"uploaded={upload_summary['uploaded']}, failed={upload_summary['failed']}",
                    )
                    if upload_summary["errors"]:
                        logger.warning(f"Map upload errors: {upload_summary['errors']}")
                    result["upload_summary"] = upload_summary
                except Exception as e:
                    progress(f"⚠️ Map upload failed: {e}", step="upload")
                    logger.exception("Map upload to Foundry failed")

            # ── Phase 5: Deploy to FoundryVTT ──
            progress("🚀 Deploying campaign to FoundryVTT...", step="deploy")
            deployment = None
            if foundry_client:
                try:
                    deployment = await self.deploy_to_foundry(campaign_data, foundry_client, asset_info, scan_result=scan_result)
                    total_deployed = sum(
                        len(deployment.get(k, []))
                        for k in ("scenes", "npcs", "journal_entries", "quest_logs", "loot_tables", "loot_piles", "playlists", "calendar_events", "encounters")
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
                            f"calendar_events={len(deployment.get('calendar_events', []))}, "
                            f"encounters={len(deployment.get('encounters', []))}"
                        ),
                    )
                except Exception as e:
                    progress(f"⚠️ Deployment failed: {e}", step="deploy")
                    result["deploy_error"] = str(e)

            # ── Phase 5b: Enrich scenes with walls/lights/sounds ──
            if foundry_client and deployment:
                progress("🏗️ Enriching scenes with walls, lights, and sounds...", step="enrich")
                try:
                    enrich_summary = await self.enrich_scenes(
                        campaign_data,
                        foundry_client,
                        deployment,
                        on_progress=on_progress,
                    )
                    enriched = enrich_summary.get("enriched", 0)
                    skipped = enrich_summary.get("skipped", 0)
                    progress(
                        f"✅ Scene enrichment complete — {enriched} scene(s) enriched, {skipped} skipped",
                        step="enrich",
                        detail=f"enriched={enriched}, skipped={skipped}, errors={len(enrich_summary.get('errors', []))}",
                    )
                    result["scene_enrichment"] = enrich_summary
                except Exception as e:
                    progress(f"⚠️ Scene enrichment failed: {e}", step="enrich")
                    logger.exception("Scene enrichment failed")

            if deployment:
                # Persist deployment data for later use by regenerate_assets
                deployment_file = campaign_assets_dir / "deployment_state.json"
                await asyncio.to_thread(
                    deployment_file.write_text,
                    json.dumps(deployment, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info(f"Saved deployment state to {deployment_file}")

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

    # ─── Arc extension ───────────────────────────────────────────────────────

    async def extend_campaign_arc(
        self,
        campaign_name: str,
        current_level: int,
        llm_client=None,
        foundry_client=None,
        vault_path: str = None,
        comfyui_url: str = None,
        omlx_url: str = None,
        omlx_api_key: str = None,
        on_progress: Callable = None,
    ) -> Dict[str, Any]:
        """Generate and deploy the next arc for an existing campaign.

        Loads the existing campaign from the vault, prompts the LLM to extend it
        with new scenes/encounters/NPCs for the next level tier, and deploys
        everything into FoundryVTT alongside the existing content.

        Args:
            campaign_name: Name of the existing campaign to extend.
            current_level: The party's current level (arc starts here).
            llm_client: httpx.AsyncClient for LLM calls.
            foundry_client: Connected FoundryClient instance.
            vault_path: Obsidian vault path.
            comfyui_url: ComfyUI URL for map generation.
            omlx_url: oMLX API URL for map generation.
            omlx_api_key: oMLX API key.
            on_progress: Optional callback(msg, step, detail).
        """
        from campaign.generator import generate_arc_extension_prompt, parse_campaign_response, validate_campaign
        import httpx

        result: Dict[str, Any] = {
            "status": "extending",
            "campaign_name": campaign_name,
            "steps": [],
        }

        def progress(msg: str, step: str = "", detail: str = ""):
            result["steps"].append({"message": msg, "step": step, "detail": detail})
            logger.info(f"[ArcExtend] {msg}")
            if on_progress:
                try:
                    on_progress(msg, step, detail)
                except Exception:
                    pass

        if llm_client is None:
            llm_client = httpx.AsyncClient(timeout=300)

        api_key = omlx_api_key or self.settings.llm_api_key

        # ── Step 1: Load existing campaign data ──
        progress("📖 Loading existing campaign data...", step="load")
        safe_name = sanitize_filename(campaign_name.lower())
        state_path = Path("./campaign_assets") / safe_name / "deployment_state.json"
        vault_json_path = (
            Path(vault_path).expanduser() / "Campaigns" / campaign_name / "campaign_data.json"
            if vault_path else None
        )

        existing_data: Dict[str, Any] = {}
        for candidate in [state_path, vault_json_path]:
            if candidate and candidate.exists():
                try:
                    existing_data = json.loads(candidate.read_text(encoding="utf-8"))
                    # deployment_state wraps campaign_data under a "campaign_data" key
                    if "campaign_data" in existing_data and isinstance(existing_data["campaign_data"], dict):
                        existing_data = existing_data["campaign_data"]
                    progress(f"✅ Loaded campaign from {candidate}", step="load")
                    break
                except Exception as e:
                    progress(f"⚠️ Could not read {candidate}: {e}", step="load")

        if not existing_data:
            result["status"] = "error"
            result["error"] = (
                f"Campaign '{campaign_name}' not found. "
                "Build the campaign first before extending it."
            )
            return result

        # Determine arc number from existing deployment state
        existing_arcs = existing_data.get("story_arcs", [])
        arc_number = sum(1 for a in existing_arcs if a.get("arc_number", 0) > 0) + 2
        if arc_number < 2:
            arc_number = 2

        progress(
            f"📐 Generating Arc {arc_number} for levels {current_level}+...",
            step="generate",
        )

        # ── Step 2: Scan Foundry for current module list ──
        active_modules: Dict[str, Any] = {}
        if foundry_client:
            try:
                scan = await self.scan_foundry_world(foundry_client)
                active_modules = scan.get("active_modules", {})
            except Exception as e:
                progress(f"⚠️ Scan skipped: {e}", step="generate")

        # ── Step 3: Generate arc via LLM ──
        arc_prompt = generate_arc_extension_prompt(
            existing_data, current_level=current_level,
            arc_number=arc_number, active_modules=active_modules,
        )

        endpoint = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": arc_prompt},
                {"role": "user", "content": (
                    f"Generate Arc {arc_number} for '{campaign_name}', "
                    f"covering levels {current_level}+."
                )},
            ],
            "temperature": 0.85,
            "max_tokens": 32768,
        }
        if "Qwen" in (self.settings.model or ""):
            payload["enable_thinking"] = False
            payload["messages"][-1]["content"] = "/nothink\n" + payload["messages"][-1]["content"]

        resp = await llm_client.post(endpoint, headers=headers, json=payload, timeout=600)
        if resp.status_code != 200:
            result["status"] = "error"
            result["error"] = f"LLM request failed: {resp.status_code} {resp.text[:500]}"
            return result

        raw_text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        arc_data = parse_campaign_response(raw_text)

        # Tag every new scene/NPC/encounter with the arc they belong to
        for section in ("scenes", "npcs", "encounters", "quest_logs", "locations", "story_arcs"):
            for item in arc_data.get(section, []):
                item["arc_number"] = arc_number

        warnings = validate_campaign(arc_data)
        for w in warnings:
            logger.warning(f"[ArcExtend] Validation: {w}")

        result["arc_data"] = arc_data
        arc_meta = arc_data.get("campaign", {})
        progress(
            f"✅ Arc {arc_number} generated — '{arc_meta.get('arc_title', 'New Arc')}' "
            f"(levels {arc_meta.get('arc_level_range', current_level)}+)",
            step="generate",
        )

        # ── Step 4: Merge arc data into existing campaign ──
        progress("🔀 Merging arc into campaign data...", step="merge")
        for section in ("scenes", "npcs", "encounters", "quest_logs", "locations",
                        "story_arcs", "journal_entries", "loot_tables", "loot_piles", "playlists"):
            existing_data.setdefault(section, [])
            existing_data[section].extend(arc_data.get(section, []))

        # ── Step 5: Save updated campaign to vault ──
        if vault_path:
            progress("💾 Saving updated campaign to vault...", step="vault")
            try:
                await self.save_to_vault(existing_data, vault_path)
                progress("✅ Vault updated", step="vault")
            except Exception as e:
                progress(f"⚠️ Vault save failed: {e}", step="vault")

        # ── Step 6: Generate maps for new scenes only ──
        progress("🎨 Generating maps for new scenes...", step="assets")
        asset_output_dir = Path("./campaign_assets") / (safe_name + "_maps")
        campaign_assets_dir = Path("./campaign_assets") / safe_name
        await asyncio.to_thread(campaign_assets_dir.mkdir, parents=True, exist_ok=True)

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

        asset_info: Dict[str, Any] = {"maps": [], "portraits": [], "status": "skipped"}
        if map_generator:
            try:
                # Only generate assets for the new arc's scenes/NPCs
                arc_only = dict(existing_data)
                arc_only["scenes"] = arc_data.get("scenes", [])
                arc_only["npcs"] = arc_data.get("npcs", [])
                arc_only["locations"] = arc_data.get("locations", [])
                asset_info = await self.generate_assets(arc_only, map_generator, asset_output_dir)
                progress(
                    f"✅ Generated {asset_info['total_maps']} map(s), {asset_info['total_portraits']} portrait(s)",
                    step="assets",
                )
            except Exception as e:
                progress(f"⚠️ Asset generation failed: {e}", step="assets")
            await map_generator.close()

        result["assets"] = asset_info

        # ── Step 7: Upload maps and deploy to Foundry ──
        if foundry_client:
            if asset_info.get("total_maps", 0) > 0:
                progress("📤 Uploading new maps to FoundryVTT...", step="upload")
                try:
                    upload_summary = await self.upload_maps_to_foundry(
                        arc_data, foundry_client, asset_output_dir, safe_name,
                    )
                    progress(
                        f"✅ Uploaded {upload_summary['uploaded']} map(s)",
                        step="upload",
                    )
                    result["upload_summary"] = upload_summary
                except Exception as e:
                    progress(f"⚠️ Map upload failed: {e}", step="upload")

            progress("🚀 Deploying new content to FoundryVTT...", step="deploy")
            try:
                deployment: Dict[str, Any] = {"scenes": [], "npcs": [], "encounters": []}
                await self.deploy_to_foundry(arc_data, foundry_client, deployment)
                progress(
                    f"✅ Deployed {len(deployment.get('scenes', []))} scenes, "
                    f"{len(deployment.get('npcs', []))} NPCs",
                    step="deploy",
                )
                # Deploy encounters for the new arc only
                enc_results = await self.deploy_encounters(arc_data, foundry_client, deployment, active_modules)
                deployment["encounters"] = enc_results
                result["deployment"] = deployment
            except Exception as e:
                progress(f"⚠️ Foundry deployment failed: {e}", step="deploy")
                result["deploy_error"] = str(e)

            # Enrich new scenes with walls/lights/sounds
            progress("🏗️ Enriching new scenes...", step="enrich")
            try:
                enrich_result = await self.enrich_scenes(
                    arc_data, foundry_client, on_progress=on_progress,
                )
                progress(
                    f"✅ Enriched {enrich_result.get('scenes_enriched', 0)} scenes",
                    step="enrich",
                )
                result["enrich_result"] = enrich_result
            except Exception as e:
                progress(f"⚠️ Scene enrichment failed: {e}", step="enrich")

        # ── Step 8: Save updated deployment state ──
        try:
            state_data = {
                "campaign_data": existing_data,
                "last_arc": arc_number,
                "last_arc_title": arc_meta.get("arc_title", ""),
                "last_extended_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            state_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            progress("💾 Deployment state saved", step="complete")
        except Exception as e:
            progress(f"⚠️ State save failed: {e}", step="complete")

        result["status"] = "complete"
        result["arc_number"] = arc_number
        result["arc_title"] = arc_meta.get("arc_title", f"Arc {arc_number}")
        return result

    # ─── Teardown ─────────────────────────────────────────────────────────────

    async def teardown_campaign(
        self,
        campaign_name: str,
        foundry_client,
    ) -> Dict[str, Any]:
        """Remove all AI-GM-created content for a campaign from FoundryVTT.

        Two deletion passes:
        1. Flag-based: deletes every world document that has flags["ai-gm"] set
           (Actors, JournalEntries, RollTables, Playlists, and Scenes).
        2. UUID-based fallback: reads the deployment state and deletes anything
           whose UUID was recorded but wasn't caught by the flag filter (e.g.
           entities created before the flag convention was stable).

        Does NOT touch the Obsidian vault or local campaign_assets files.
        """
        result: Dict[str, Any] = {
            "campaign_name": campaign_name,
            "deleted": {},
            "errors": [],
            "status": "ok",
        }

        if not foundry_client or not foundry_client.is_connected:
            result["status"] = "error"
            result["errors"].append("Not connected to FoundryVTT")
            return result

        # ── Pass 1: delete everything flagged with flags["ai-gm"] ──────────
        # Runs in a single execute-js call so it's one round-trip regardless
        # of how many entities exist.
        js = r"""
const results = {};
const collections = [
  ["actors",    game.actors],
  ["journal",   game.journal],
  ["tables",    game.tables],
  ["playlists", game.playlists],
  ["scenes",    game.scenes],
];
for (const [label, col] of collections) {
  const toDelete = col.filter(d => d.flags?.["ai-gm"]).map(d => d.id);
  results[label] = toDelete.length;
  if (toDelete.length > 0) {
    await col.documentClass.deleteDocuments(toDelete);
  }
}
return results;
"""
        try:
            js_result = await foundry_client.execute_js(js)
            # execute-js-result wraps the JS return value in {"result": ...}
            counts = js_result.get("result") if isinstance(js_result, dict) else None
            if not isinstance(counts, dict):
                counts = {}
            result["deleted"]["flag_pass"] = counts
            total = sum(v for v in counts.values() if isinstance(v, int))
            logger.info(f"[Teardown] Flag pass deleted {total} documents: {counts}")
        except Exception as e:
            logger.warning(f"[Teardown] Flag pass failed: {e}")
            result["errors"].append(f"flag_pass: {e}")

        # ── Pass 2: UUID fallback from deployment state ─────────────────────
        safe_name = sanitize_filename(campaign_name.lower())
        state_path = Path("./campaign_assets") / safe_name / "deployment_state.json"

        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                # Collect all UUIDs from every tracked section
                uuids: Dict[str, list] = {}
                section_type_map = {
                    "scenes":          "Scene",
                    "npcs":            "Actor",
                    "journal_entries": "JournalEntry",
                    "quest_logs":      "JournalEntry",
                    "loot_tables":     "RollTable",
                    "loot_piles":      "Actor",
                    "encounter_actors": "Actor",
                    "playlists":       "Playlist",
                }
                for section, doc_type in section_type_map.items():
                    for item in state.get(section, []):
                        uuid = item.get("uuid", "")
                        if uuid:
                            uuids.setdefault(doc_type, []).append(uuid)

                if uuids:
                    uuids_json = json.dumps(uuids)
                    fallback_js = f"""
const uuidMap = {uuids_json};
const typeMap = {{
  "Scene": game.scenes,
  "Actor": game.actors,
  "JournalEntry": game.journal,
  "RollTable": game.tables,
  "Playlist": game.playlists,
}};
const fbResults = {{}};
for (const [docType, uuids] of Object.entries(uuidMap)) {{
  const col = typeMap[docType];
  if (!col) continue;
  const ids = uuids.map(u => u.split(".").pop()).filter(id => col.get(id));
  fbResults[docType] = ids.length;
  if (ids.length > 0) await col.documentClass.deleteDocuments(ids);
}}
return fbResults;
"""
                    try:
                        fb_result = await foundry_client.execute_js(fallback_js)
                        fb_counts = fb_result.get("result") if isinstance(fb_result, dict) else None
                        if not isinstance(fb_counts, dict):
                            fb_counts = {}
                        result["deleted"]["uuid_pass"] = fb_counts
                        fb_total = sum(v for v in fb_counts.values() if isinstance(v, int))
                        logger.info(f"[Teardown] UUID fallback deleted {fb_total} more documents: {fb_counts}")
                    except Exception as e:
                        logger.warning(f"[Teardown] UUID pass failed: {e}")
                        result["errors"].append(f"uuid_pass: {e}")
            except Exception as e:
                logger.warning(f"[Teardown] Could not read deployment state: {e}")
                result["errors"].append(f"state_read: {e}")

        if result["errors"]:
            result["status"] = "partial"
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
