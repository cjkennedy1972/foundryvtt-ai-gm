"""Scene Awareness — loads and manages scene data (tokens, tiles, layout)."""

import asyncio
import json
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from foundry.client import FoundryClient
from state.tracker import GameStateTracker
from context.loader import CampaignLoader

logger = logging.getLogger(__name__)

# Maximum number of scenes to keep in memory cache (LRU eviction)
MAX_CACHED_SCENES = 10


class SceneAwareness:
    """Manages scene data — tokens, tiles, environment details.

    Uses LRU cache to bound memory: only keeps MAX_CACHED_SCENES in memory.
    Older scenes are evicted and reloaded from Foundry when needed.
    """

    def __init__(
        self,
        foundry: FoundryClient,
        state_tracker: GameStateTracker,
        campaign_loader: Optional[CampaignLoader] = None,
        llm_manager=None,
    ):
        self.foundry = foundry
        self.state_tracker = state_tracker
        self.campaign_loader = campaign_loader
        self._llm_manager = llm_manager
        # Use OrderedDict for LRU behavior: oldest items at head, newest at tail
        self._scene_data: OrderedDict[str, Any] = OrderedDict()
        self._current_scene: Optional[str] = None
        self._scene_familiarity: Dict[str, int] = {}  # scene_name -> familiarity level
        self._on_scene_change_callback: Optional[any] = None

    def _cache_scene(self, scene_name: str, scene_context: Dict[str, Any]):
        """Cache a scene, evicting oldest if cache is full (LRU)."""
        # Move to end (marks as most recently used)
        if scene_name in self._scene_data:
            self._scene_data.move_to_end(scene_name)
        else:
            self._scene_data[scene_name] = scene_context
            # Evict oldest (leftmost) if cache exceeds max size
            if len(self._scene_data) > MAX_CACHED_SCENES:
                oldest_scene = next(iter(self._scene_data))
                del self._scene_data[oldest_scene]
                logger.info(f"[Scene] Evicted {oldest_scene} from cache (LRU, limit={MAX_CACHED_SCENES})")

        # Update reference
        self._scene_data[scene_name] = scene_context

    async def load_scene(self, scene_name: str) -> Dict[str, Any]:
        """Load all data for a scene from FoundryVTT."""
        logger.info(f"[Scene] Loading scene: {scene_name}")

        try:
            # Get scene details from Foundry
            details = await self.foundry.get_scene_details(scene_name)

            # Get tokens with positions
            tokens = await self.foundry.get_scene_tokens(scene_name)

            # Build scene context with real timestamp
            now = datetime.now(timezone.utc).isoformat()
            scene_context = {
                "name": scene_name,
                "tokens": tokens,
                "details": details,
                "loaded_at": now,
            }

            # Store in state tracker
            await self.state_tracker.set_scene_data({
                "name": scene_name,
                "token_count": len(tokens),
                "tokens": tokens,
                "loaded_at": now,
            })

            # Update cache AFTER successful load (tokens/details are ready)
            # Uses LRU eviction if cache exceeds MAX_CACHED_SCENES
            self._cache_scene(scene_name, scene_context)
            self._current_scene = scene_name

            # Mark scene as explored
            if scene_name not in self._scene_familiarity:
                self._scene_familiarity[scene_name] = 1
            else:
                self._scene_familiarity[scene_name] += 1

            logger.info(
                f"[Scene] Loaded {scene_name}: {len(tokens)} tokens"
            )

            # Notify callback
            if self._on_scene_change_callback:
                await self._on_scene_change_callback({
                    "type": "scene_loaded",
                    "scene_name": scene_name,
                    "token_count": len(tokens),
                    "familiarity": self._scene_familiarity[scene_name]
                })

            return scene_context

        except Exception as e:
            logger.error(f"[Scene] Failed to load scene {scene_name}: {e}", exc_info=True)
            return {}

    async def on_scene_change(self, scene_name: str):
        """Handle scene change events from FoundryVTT."""
        logger.info(f"[Scene] Scene changed to: {scene_name}")

        # Check if we've seen this scene before
        if self._current_scene == scene_name:
            logger.info(f"[Scene] Already on scene: {scene_name}")
            return

        self._current_scene = scene_name
        self.state_tracker.set_scene(scene_name)

        # Check familiarity level
        familiar = self._scene_familiarity.get(scene_name, 0)

        if familiar == 0:
            # First visit — full load
            await self.load_scene(scene_name)
        else:
            # Returning visit — refresh token positions
            await self.refresh_scene_tokens(scene_name)

        # Notify chat listener to update NPC context and encounter briefs
        if self.campaign_loader:
            npc_context = self.campaign_loader.get_npc_context_sync()
            await self.state_tracker.set_npc_context(npc_context)
            if self._llm_manager:
                self._llm_manager.set_dynamic_npc_context(npc_context or "")
                world_context = self.campaign_loader.get_world_context_sync()
                self._llm_manager.set_dynamic_world_context(world_context or "")

            # Store encounter context for this scene so chat_listener can inject it
            enc_context = self.campaign_loader.get_encounter_context_for_scene(scene_name)
            if enc_context:
                await self.state_tracker.set_encounter_context(enc_context)
            else:
                await self.state_tracker.set_encounter_context("")

    async def refresh_scene_tokens(self, scene_name: str) -> List[Dict[str, Any]]:
        """Refresh token positions for a scene without full reload."""
        try:
            tokens = await self.foundry.get_scene_tokens(scene_name)
            now = datetime.now(timezone.utc).isoformat()
            await self.state_tracker.set_scene_data({
                "name": scene_name,
                "token_count": len(tokens),
                "tokens": tokens,
                "updated_at": now,
            })

            # Update cached scene with new tokens (and cache it if not already cached)
            if scene_name in self._scene_data:
                self._scene_data[scene_name]["tokens"] = tokens
                self._scene_data[scene_name]["updated_at"] = now
                # Mark as recently used for LRU
                self._scene_data.move_to_end(scene_name)
            else:
                # Scene was evicted, recreate minimal cache entry
                scene_context = {
                    "name": scene_name,
                    "tokens": tokens,
                    "loaded_at": now,
                }
                self._cache_scene(scene_name, scene_context)

            logger.info(f"[Scene] Refreshed {scene_name}: {len(tokens)} tokens")
            return tokens
        except Exception as e:
            logger.error(f"[Scene] Failed to refresh tokens: {e}", exc_info=True)
            return []

    async def get_scene_description(self, scene_name: str = None) -> str:
        """Generate a scene description for LLM context."""
        if not scene_name:
            scene_name = self.state_tracker.state.current_scene
            if not scene_name:
                return "Unknown scene — no location data available."

        scene_info = self._scene_data.get(scene_name, {})
        tokens = scene_info.get("tokens", [])
        familiarity = self._scene_familiarity.get(scene_name, 0)

        # Build description from tokens
        token_descriptions = []
        for token in tokens:
            name = token.get("name", "Unknown")
            x = token.get("x", 0)
            y = token.get("y", 0)
            disp = token.get("disposition", 1)
            side = "ally/PC" if disp >= 0 else "hostile"
            token_descriptions.append(f"- {name} ({side}) at position ({x}, {y})")

        description = f"## Scene: {scene_name}\n"
        description += f"**Familiarity:** {'Known' if familiarity > 1 else 'Explored once'}\n"
        description += f"**Entities on scene ({len(tokens)}):**\n"
        description += "\n".join(token_descriptions)

        return description

    def get_familiarity(self, scene_name: str) -> int:
        """Get familiarity level for a scene (0 = unexplored, higher = more visits)."""
        return self._scene_familiarity.get(scene_name, 0)

    def set_scene_change_callback(self, callback):
        """Set callback for scene change events."""
        self._on_scene_change_callback = callback

    def get_context_summary(self) -> str:
        """Get a compact summary of the current scene for LLM context."""
        if not self._current_scene:
            return ""

        scene_info = self._scene_data.get(self._current_scene, {})
        tokens = scene_info.get("tokens", [])

        lines = [f"**Current Scene:** {self._current_scene}"]
        if tokens:
            lines.append(f"**Entities on scene:** {len(tokens)}")
            # Group by disposition
            hostiles = [t for t in tokens if t.get("disposition", 1) < 0]
            friends = [t for t in tokens if t.get("disposition", 1) >= 0]
            if hostiles:
                hostile_names = [t.get("name", "?") for t in hostiles]
                lines.append(f"**Hostiles:** {', '.join(hostile_names)}")
            if friends:
                friend_names = [t.get("name", "?") for t in friends]
                lines.append(f"**Allies/PCs:** {', '.join(friend_names)}")

        return "\n".join(lines)
