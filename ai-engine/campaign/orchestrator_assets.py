"""Image generation and upload for campaign scenes, NPCs and the prologue.

Extracted verbatim from campaign/orchestrator.py, which had grown to 4,469
lines in a single class — 9x this project's 500-line limit. Mixins rather than
free functions so every `self.` call inside these methods keeps working
unchanged; CampaignOrchestrator composes them.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from campaign.assets import upload_image
from campaign.layout_generator import validate_scene_setup
from utils.path_safety import sanitize_filename

logger = logging.getLogger(__name__)


class AssetPipelineMixin:
    """Image generation and upload for campaign scenes, NPCs and the prologue."""

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

        await self._generate_scene_maps(location_scenes, map_generator, output_dir, results)

        # Generate maps for locations
        locations = campaign_data.get("locations", [])
        location_maps = [l for l in locations if l.get("map_needed")]

        await self._generate_location_maps(location_maps, map_generator, output_dir, results)

        # Generate NPC portraits
        # portrait_needed=None means "not explicitly set" — treat as True so campaigns
        # built with partial errors still get portraits on regenerate.
        npcs = campaign_data.get("npcs", [])
        portrait_npcs = [n for n in npcs if n.get("portrait_needed") is not False]

        await self._generate_portraits(portrait_npcs, map_generator, output_dir, results)

        # ── Generate prologue panel illustrations ──
        prologue = campaign_data.get("prologue")
        await self._generate_prologue_panels(prologue, map_generator, output_dir, results)

        results["total_maps"] = len(results["maps"])
        results["total_portraits"] = len(results["portraits"])
        results["total_prologue_panels"] = len(results.get("prologue_panels", []))
        return results


    async def _generate_scene_maps(self, location_scenes, map_generator, output_dir, results) -> None:
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

                # Validate scene_setup geometry — fall back to procedural generation
                # if walls are disconnected, out-of-bounds, or otherwise invalid.
                scene_type = scene.get("type", "dungeon")
                scene["_scene_type"] = scene_type
                needs_fallback = False
                if walls or doors:
                    is_valid, val_warnings = validate_scene_setup(setup)
                    if not is_valid:
                        logger.warning(
                            f"[Layout] Scene '{scene['name']}' scene_setup failed validation "
                            f"({len(val_warnings)} issue(s)): {val_warnings} — activating procedural fallback"
                        )
                        needs_fallback = True

                if (walls or doors) and not needs_fallback:
                    logger.info(f"[Layout] Scene '{scene['name']}' has valid wall/door data — using ControlNet layout-guided generation")
                    # Set _output_dir so generate_layout_mask can save to the right place
                    scene["_output_dir"] = str(output_dir)
                    try:
                        if needs_fallback:
                            logger.info(f"[Layout] Using procedural fallback layout for '{scene['name']}'")
                            layout_mask = await map_generator.fallback_layout_for_scene(
                                scene=scene,
                                width=img_w,
                                height=img_h,
                                grid_size_px=gp,
                            )
                        else:
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
                            logger.info(f"[Layout] No layout data to mask — falling back to text-only generation (scene: '{scene['name']}')")
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

    async def _generate_location_maps(self, location_maps, map_generator, output_dir, results) -> None:
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

    async def _generate_portraits(self, portrait_npcs, map_generator, output_dir, results) -> None:
        if portrait_npcs:
            logger.info(f"Generating {len(portrait_npcs)} NPC portrait(s)...")
            portraits_dir = output_dir / "portraits"
            portraits_dir.mkdir(exist_ok=True)

            for npc in portrait_npcs:
                prompt = npc.get("description", f"{npc.get('name', 'NPC')} portrait")
                # Per-NPC filename: the raw ComfyUI output name is timestamp-based
                # and collided across NPCs (two actors ended up sharing one image).
                portrait_file = portraits_dir / f"portrait_{sanitize_filename(npc['name'].lower())}.png"

                try:
                    portrait_result = await map_generator.generate_portrait(
                        prompt=prompt,
                        output_dir=portraits_dir,
                    )
                    if portrait_result["status"] == "success":
                        src_file = Path(portrait_result["output_file"])
                        if src_file != portrait_file:
                            await asyncio.to_thread(src_file.replace, portrait_file)
                        results["portraits"].append({
                            "npc": npc["name"],
                            "file": str(portrait_file),
                            "provider": portrait_result.get("provider", "unknown"),
                        })
                        npc["portrait_file"] = portrait_file.name
                    else:
                        logger.warning(f"Portrait generation failed for {npc['name']}: {portrait_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Portrait generation error for {npc['name']}: {e}")

    async def _generate_prologue_panels(self, prologue, map_generator, output_dir, results) -> None:
        if prologue and isinstance(prologue, dict):
            panels = prologue.get("panels", [])
            vessel = prologue.get("vessel", "tome")
            if panels:
                logger.info(f"Generating {len(panels)} prologue panel illustration(s) (vessel: {vessel})...")
                prologue_dir = output_dir / "prologue"
                prologue_dir.mkdir(exist_ok=True)

                for i, panel in enumerate(panels):
                    if not isinstance(panel, dict):
                        continue
                    image_prompt = panel.get("image_prompt", "")
                    if not image_prompt:
                        logger.warning(f"Prologue panel {i+1} missing 'image_prompt', skipping")
                        continue

                    # Landscape aspect for journal image pages (~1344x768)
                    panel_filename = f"prologue_panel_{i+1:02d}.png"
                    panel_path = prologue_dir / panel_filename

                    try:
                        # Use dedicated prologue panel generation with vessel style
                        panel_result = await map_generator.generate_prologue_panel(
                            prompt=image_prompt,
                            vessel=vessel,
                            output_dir=prologue_dir,
                            width=1344,
                            height=768,
                        )
                        if panel_result.get("status") == "success":
                            src_file = Path(panel_result["output_file"])
                            if src_file != panel_path:
                                await asyncio.to_thread(src_file.replace, panel_path)
                            results.setdefault("prologue_panels", []).append({
                                "panel_index": i,
                                "title": panel.get("title", f"Panel {i+1}"),
                                "file": str(panel_path),
                                "provider": panel_result.get("provider", "unknown"),
                            })
                            # Stash the served path on the panel dict for later upload
                            panel["image_file"] = panel_path.name
                            logger.info(f"Prologue panel {i+1} generated: {panel_path.name}")
                        else:
                            logger.warning(f"Prologue panel {i+1} generation failed: {panel_result.get('error', 'unknown')}")
                    except Exception as e:
                        logger.warning(f"Prologue panel {i+1} generation error: {e}")
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

        # Sequential — concurrent uploads overwhelm the relay/Foundry WebSocket
        # and 408 out (same fix as regenerate_assets_for_campaign).
        for scene in scenes:
            map_file = scene.get("map_file")
            if not map_file:
                continue
            img_path = asset_output_dir / map_file
            if not img_path.exists():
                logger.warning(f"[Upload] Map file not found: {img_path}")
                summary["failed"] += 1
                summary["errors"].append(f"{scene.get('name', '?')}: file not found ({img_path.name})")
                continue
            result = await upload_image(
                foundry_client, img_path, f"ai-gm-maps/{safe_name}", map_file,
                f"ai-gm-maps/{safe_name}/{map_file}",
            )
            if result["ok"]:
                scene["background_src"] = result["src"]
                summary["uploaded"] += 1
                logger.info(f"[Upload] '{scene.get('name', '?')}' → {result['src']}")
            else:
                summary["failed"] += 1
                msg = f"{scene.get('name', '?')}: {result['error']}"
                summary["errors"].append(msg)
                logger.warning(f"[Upload] Map upload failed: {msg}")
        logger.info(
            f"[Upload] Map upload complete: {summary['uploaded']} uploaded, "
            f"{summary['failed']} failed"
        )
        return summary

    async def upload_portraits_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_output_dir: Path,
        safe_name: str,
    ) -> Dict[str, Any]:
        """Upload generated NPC portrait PNGs and set portrait_src on each NPC dict.

        Must run AFTER generate_assets() (which populates npc["portrait_file"]) and
        BEFORE deploy_to_foundry() (which reads npc["portrait_src"] when creating actors).
        """
        summary: Dict[str, Any] = {"uploaded": 0, "failed": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — portrait upload skipped")
            return summary

        # Sequential for the same 408 reason as maps.
        for npc in campaign_data.get("npcs", []):
            portrait_file = npc.get("portrait_file")
            if not portrait_file:
                continue
            img_path = asset_output_dir / "portraits" / portrait_file
            if not img_path.exists():
                continue
            result = await upload_image(
                foundry_client, img_path, f"ai-gm-portraits/{safe_name}", portrait_file,
                f"ai-gm-portraits/{safe_name}/{portrait_file}",
            )
            if result["ok"]:
                npc["portrait_src"] = result["src"]
                summary["uploaded"] += 1
                logger.info(f"[Upload] Portrait '{npc.get('name', '?')}' → {result['src']}")
            else:
                summary["failed"] += 1
                msg = f"{npc.get('name', '?')}: {result['error']}"
                summary["errors"].append(msg)
                logger.warning(f"[Upload] Portrait upload failed: {msg}")

        return summary

    async def upload_prologue_to_foundry(
        self,
        campaign_data: Dict[str, Any],
        foundry_client,
        asset_output_dir: Path,
        safe_name: str,
    ) -> Dict[str, Any]:
        """Upload generated prologue panel PNGs to Foundry.

        Must run AFTER generate_assets() (which populates panel["image_file"]) and
        BEFORE deploy_to_foundry() so the JournalEntry pages reference the correct src.
        """
        summary: Dict[str, Any] = {"uploaded": 0, "failed": 0, "errors": []}

        if not foundry_client or not getattr(foundry_client, "is_connected", False):
            summary["errors"].append("Foundry not connected — prologue upload skipped")
            return summary

        prologue = campaign_data.get("prologue")
        if not prologue or not isinstance(prologue, dict):
            return summary

        panels = prologue.get("panels", [])
        if not panels:
            return summary

        # Sequential upload to avoid 408s
        for i, panel in enumerate(panels):
            image_file = panel.get("image_file")
            if not image_file:
                continue
            img_path = asset_output_dir / "prologue" / image_file
            if not img_path.exists():
                logger.warning(f"Prologue panel {i+1} file not found: {img_path}")
                continue
            result = await upload_image(
                foundry_client, img_path, f"ai-gm-prologue/{safe_name}", image_file,
                f"ai-gm-prologue/{safe_name}/{image_file}",
            )
            if result["ok"]:
                panel["image_src"] = result["src"]
                summary["uploaded"] += 1
                logger.info(f"[Upload] Prologue panel {i+1} → {result['src']}")
            else:
                summary["failed"] += 1
                msg = f"Prologue panel {i+1}: {result['error']}"
                summary["errors"].append(msg)
                logger.warning(f"[Upload] Prologue panel upload failed: {msg}")

        return summary

    async def _attach_map_to_scene(self, foundry_client, scene: dict, src: str, summary: dict) -> None:
        """Push a new background src into an ALREADY-DEPLOYED scene, by name.

        Only for regenerate — build-time scenes don't exist in Foundry yet,
        deploy_to_foundry creates them fresh reading scene["background_src"].
        """
        try:
            # FoundryVTT v14: Attach background via the Levels system
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

    async def _attach_portrait_to_actor(
        self, foundry_client, npc: dict, src: str, npc_uuid_map: dict, summary: dict
    ) -> None:
        """Push a new portrait src into an ALREADY-DEPLOYED actor, by uuid or name.

        Only for regenerate — build-time NPCs don't exist in Foundry yet,
        deploy_to_foundry creates them fresh reading npc["portrait_src"].
        """
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
        from campaign.vault import CampaignStore

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
        store = CampaignStore(campaign_name, self.settings.campaign_vault_path)
        if not store.exists:
            summary["status"] = "error"
            summary["errors"].append(f"Campaign '{campaign_name}' not found in vault")
            return summary
        campaign_data = await store.load(normalize=False)

        # ── Load deployment state (NPC UUIDs from last deployment) ──
        deployment_state = await store.load_deployment()
        npc_uuid_map = {  # name -> uuid
            npc_info["name"]: npc_info["uuid"]
            for npc_info in deployment_state.get("npcs", [])
            if npc_info.get("status") == "created" and npc_info.get("uuid")
        }
        if npc_uuid_map:
            logger.info(f"Loaded deployment state with {len(npc_uuid_map)} NPC UUIDs")

        # ── Generate images (improved SDXL workflow) ──
        safe_name = store.safe_name
        asset_output_dir = store.maps_dir

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
                    map_file = scene.get("map_file")
                    if not map_file:
                        return
                    img_path = asset_output_dir / map_file
                    if not img_path.exists():
                        return
                    result = await upload_image(
                        foundry_client, img_path, f"ai-gm-maps/{safe_name}", map_file,
                        f"ai-gm-maps/{safe_name}/{map_file}",
                    )
                    if not result["ok"]:
                        msg = f"scene '{scene.get('name', '?')}': {result['error']}"
                        logger.warning(f"File upload/processing failed: {msg}")
                        summary["errors"].append(msg)
                        return
                    scene["background_src"] = result["src"]
                    await self._attach_map_to_scene(foundry_client, scene, result["src"], summary)

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
                        portrait_file = npc.get("portrait_file")
                        if not portrait_file:
                            return
                        portrait_path = asset_output_dir / "portraits" / portrait_file
                        if not portrait_path.exists():
                            return
                        result = await upload_image(
                            foundry_client, portrait_path, f"ai-gm-portraits/{safe_name}", portrait_file,
                            f"ai-gm-portraits/{safe_name}/{portrait_file}",
                        )
                        if not result["ok"]:
                            msg = f"NPC '{npc.get('name', '?')}': {result['error']}"
                            logger.warning(f"Portrait upload/processing failed: {msg}")
                            summary["errors"].append(msg)
                            return
                        logger.info(f"Using portrait source: {result['src']}")
                        npc["portrait_src"] = result["src"]
                        await self._attach_portrait_to_actor(foundry_client, npc, result["src"], npc_uuid_map, summary)

                    # Upload portraits sequentially — same reason as the maps
                    # above: concurrent uploads overwhelm the relay/Foundry and
                    # 408 out (Elara's portrait was lost to exactly this).
                    for npc in npc_list:
                        await _upload_and_attach_portrait(npc)

                # ── Restore walls/lights/sounds on scenes missing them ──
                # Scenes deployed via the deploy endpoint historically never got
                # enriched; enrich_scenes skips categories that already exist.
                if deployment_state.get("scenes"):
                    try:
                        _progress("🏗️ Verifying scene walls, lights, and sounds...", step="enrich")
                        enrich_summary = await self.enrich_scenes(
                            campaign_data, foundry_client, deployment_state,
                        )
                        summary["scenes_enriched"] = enrich_summary.get("enriched", 0)
                        summary["errors"].extend(enrich_summary.get("errors", []))
                    except Exception as e:
                        logger.warning(f"Enrichment during regenerate failed: {e}")
                        summary["errors"].append(f"enrich: {e}")
            elif attach_to_foundry and not connected:
                summary["errors"].append(
                    "Foundry not connected — images regenerated and saved, but not attached to scenes/NPCs"
                )
        finally:
            await map_generator.close()

        # ── Persist updated references back to the vault ──
        await store.save(campaign_data)
        _progress(
            f"✅ Regenerated {summary['maps_generated']} map(s), "
            f"{summary['portraits_generated']} portrait(s), "
            f"attached {summary['scenes_attached']} scenes and {summary.get('portraits_attached', 0)} portraits to Foundry",
            step="assets",
        )
        return summary

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

        from foundry import scripts

        try:
            res = await foundry_client.execute_js(scripts.find_actors_needing_portraits())
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
                            result = await upload_image(
                                foundry_client, pfile, f"ai-gm-portraits/{safe_name}", pfile.name,
                                f"ai-gm-portraits/{safe_name}/{pfile.name}",
                            )
                            if result["ok"]:
                                src = result["src"]
                            else:
                                summary["errors"].append(f"{name}: {result['error']}")
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
