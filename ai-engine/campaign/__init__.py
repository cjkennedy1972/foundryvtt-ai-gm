"""
Campaign Builder — Generate D&D campaigns from natural language prompts.

Pipeline:
  1. User provides prompt (via admin panel)
  2. LLM generates structured campaign data (NPCs, locations, quests, story arcs)
  3. Campaign saved to Obsidian vault as structured markdown with wikilinks
  4. ComfyUI generates map images for each location
  5. Maps uploaded to FoundryVTT as scene maps
  6. Journal entries and quests created for tracking
"""

import logging

logger = logging.getLogger(__name__)
