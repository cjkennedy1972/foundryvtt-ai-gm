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

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
