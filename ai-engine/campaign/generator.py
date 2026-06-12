"""
Campaign Generator — LLM-driven campaign structure generation.

Takes a user prompt and generates structured campaign data:
- Campaign metadata (name, theme, tone, level_range)
- NPCs with stats, motivations, relationships
- Locations with descriptions, maps needed, connections
- Quests with objectives, rewards, story progression
- Story arcs with acts, milestones, major events
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# System prompt for campaign generation — forces structured JSON output
CAMPAIGN_GENERATOR_PROMPT = """You are a Campaign Architect for a TTRPG GM system. Your job is to design complete, playable campaigns from natural language prompts.

## How You Respond

You respond with a SINGLE JSON object containing the full campaign structure.

```json
{
  "campaign": {
    "name": "The Shattered Crown",
    "theme": "Political intrigue, ancient magic, redemption",
    "tone": "Dark fantasy with glimmers of hope",
    "level_range": "3-10",
    "estimated_sessions": "12-16",
    "description": "A paragraph describing the campaign premise...",
    "setting_type": "medieval-fantasy",
    "pantheon": "Polytheistic pantheon with 5 major deities",
    "magic_level": "standard"
  },
  "story_arcs": [
    {
      "act": 1,
      "title": "The Gathering Storm",
      "description": "Characters are drawn together by...",
      "milestones": [
        {"name": "First meeting", "description": "The party assembles in..."},
        {"name": "The inciting incident", "description": "A messenger arrives with..."}
      ],
      "climax": "The first major confrontation at...",
      "transition_to_act2": "Victory reveals a larger threat..."
    }
  ],
  "npcs": [
    {
      "name": "Elder Morwenna",
      "role": "mentor",
      "faction": "Circle of Elders",
      "alignment": "LG",
      "description": "A wise but weary herbalist who knows more than she lets on.",
      "personality": ["patient", "secretive", "protective"],
      "motivations": ["Protect the ancient secret", "Guide the heroes"],
      "relationships": ["dislikes Baron Vex", "trusted by the village"],
      "stat_block": "CR 2 — ancient enchantress, powerful in nature magic",
      "first_appearance": "Act 1, Scene 2 — the village gathering"
    }
  ],
  "locations": [
    {
      "name": "Riverbend Village",
      "type": "village",
      "act": 1,
      "description": "A small farming village on the banks of the Silverstream.",
      "key_features": ["Old mill", "Village elder's house", "Cursed well"],
      "connections": ["Forest path to Ruins of Valdor (2h)", "Road to Oakhaven (half day)"],
      "map_needed": true,
      "map_style": "top-down village overview, misty morning, stone cottages, river"
    }
  ],
  "quests": [
    {
      "id": "quest_1",
      "title": "The Cursed Well",
      "type": "main",
      "act": 1,
      "description": "The village well has begun producing black water that drives livestock mad.",
      "objectives": [
        {"desc": "Investigate the well at night", "check": "Perception DC 14"},
        {"desc": "Discover the corrupted spirit of the old water nymph", "check": "Arcana DC 16"},
        {"desc": "Purge the corruption with a ritual", "check": "Religion DC 15"}
      ],
      "rewards": ["Village gratitude (+1 to reputation)", "Ancient water rune artifact"],
      "consequences": {"success": "Village is safe, NPC gains trust", "failure": "Corruption spreads, NPCs turn hostile"},
      "prerequisite": null,
      "leads_to": "quest_2"
    }
  ],
  "factions": [
    {
      "name": "The Obsidian Hand",
      "description": "A secret society of warlocks seeking to overthrow the aristocracy.",
      "alignment": "NE",
      "goals": ["Infiltrate every noble house", "Awaken the fallen god beneath the capital"],
      "strength": "moderate",
      "members": 12
    }
  ],
  "artifacts": [
    {
      "name": "The Shattered Crown",
      "description": "The broken diadem of the last Elven king. Each fragment grants a different power.",
      "type": "legendary",
      "fragments": 3,
      "fragment_powers": [
        "Wielder can see through deception",
        "Wielder commands plant and stone",
        "Wielder can command the dead"
      ],
      "current_locations": ["ruins act 2", "villain lair act 3", "hidden vault act 4"]
    }
  ]
}
```

## Campaign Design Principles

1. **Structure**: 3-5 acts with clear progression. Each act has a beginning, middle, climax, and hook for the next act.
2. **NPCs**: Each NPC should have clear motivations, personality traits, and relationships to others. They should feel alive, not like quest dispensers.
3. **Locations**: Each location should have a distinct visual identity, key features, and connections to other places. Include map style hints for AI generation.
4. **Quests**: Quests should have clear objectives, interesting challenges (not just combat), meaningful rewards, and consequences. Use the 3-test structure: investigate, discover, resolve.
5. **Story Arcs**: Build thematic arcs — personal (character growth), political (faction struggles), and cosmic (larger threats).
6. **Pacing**: Mix quiet moments with action. Give players room to make choices that matter.
7. **Player Agency**: Design hooks that lead to multiple possible paths. Never railroad.
8. **Hooks for Customization**: Include 2-3 "insert your players here" moments where the GM can adapt to their party's composition and backstory.

## Map Style Guidelines

When generating `map_style` hints for locations, use these conventions:
- **Tone**: "top-down dungeon map, gritty parchment style" or "isometric city overview, vibrant watercolor" or "aerial view, misty mountains, ethereal glow"
- **Elements**: Include key landmarks, terrain features, and notable structures
- **Scale**: Specify if it's a "room-scale" (for combat) or "exploration-scale" (for travel)
- **Lighting**: Mention if it's "dark with torchlight spots" or "bright daylight"

## CRITICAL OUTPUT RULES

- **OUTPUT ONLY THE JSON OBJECT.** No thinking, no reasoning, no explanation.
- **Start your response directly with `{`** and end with `}`.
- **Do NOT output any text before or after the JSON.**
- **Be a JSON object from the very first character.**
"""


def generate_campaign_prompt(user_input: str) -> str:
    """Build the full prompt for the LLM campaign generator."""
    return f"""You are designing a TTRPG campaign based on this request:

"{user_input}"

Use your creativity to design a complete, playable campaign. Include:
- A compelling premise and setting
- 5-8 NPCs with distinct personalities and motivations
- 4-6 locations (mix of towns, dungeons, wilderness, mystical places)
- 3-5 quests per story arc (with investigation → discovery → resolution structure)
- 3-5 story arcs forming a cohesive narrative
- At least 1 faction with hidden agendas
- At least 1 multi-part artifact or McGuffin

Design for a group of 3-4 players at levels 1-5, adjustable up to 10.

Remember: great campaigns are defined by consequences, not just plot. Every choice should matter.

{CAMPAIGN_GENERATOR_PROMPT}
"""


def parse_campaign_response(raw_text: str) -> Dict[str, Any]:
    """Extract and validate JSON campaign data from LLM response.

    Handles:
    - Raw JSON output
    - ```json...``` code blocks
    - Chain-of-thought preambles
    - Malformed trailing text
    """
    # Step 1: Strip ```json...``` code blocks
    result = raw_text.strip()
    json_match = re.search(r'```json\s*\n(.*?)```', result, re.DOTALL)
    if json_match:
        result = json_match.group(1)
    else:
        # Also try ``` without json tag
        json_match = re.search(r'```\s*\n(.*?)```', result, re.DOTALL)
        if json_match:
            result = json_match.group(1)

    # Step 2: Try balanced brace extraction
    if not _is_valid_json(result):
        brace_result = _extract_balanced_json(result)
        if brace_result:
            result = brace_result

    # Step 3: Parse
    try:
        data = json.loads(result)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse campaign JSON: {e}")
        # Try to recover: find the largest valid JSON object
        recovery = _try_recovery_json(result)
        if recovery:
            return recovery
        raise


def _is_valid_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def _extract_balanced_json(text: str) -> Optional[str]:
    """Extract the largest balanced JSON object from text using brace counting."""
    open_brace = text.find('{')
    if open_brace == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i in range(open_brace, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == '\\':
            if in_string:
                escape_next = True
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[open_brace:i + 1].strip()

    return None


def _try_recovery_json(text: str) -> Optional[Dict]:
    """Try to extract campaign data by finding known top-level keys."""
    # Common campaign JSON top-level keys
    key_patterns = [
        r'"(campaign|story_arcs|npcs|locations|quests)"',
        r'(?:^|,)\s*"([^"]+)"\s*:',
    ]

    # Try to find the first major key and extract its value
    for match in re.finditer(r'(?:^|,)\s*"(campaign|story_arcs|npcs|locations|quests|factions|artifacts)"\s*:', text):
        start = match.start()
        # Find the opening brace after the key
        brace_pos = text.find('{', match.end() - 1)
        if brace_pos != -1:
            chunk = _extract_balanced_json(text[brace_pos:])
            if chunk:
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue
    return None


def validate_campaign(data: Dict[str, Any]) -> List[str]:
    """Validate campaign structure. Returns list of warnings."""
    warnings = []

    campaign = data.get("campaign", {})
    if not campaign.get("name"):
        warnings.append("Campaign missing 'name' field")
    if not campaign.get("description"):
        warnings.append("Campaign missing 'description' field")

    npcs = data.get("npcs", [])
    if len(npcs) < 3:
        warnings.append(f"Only {len(npcs)} NPCs defined (recommended: 5-8)")

    locations = data.get("locations", [])
    if len(locations) < 3:
        warnings.append(f"Only {len(locations)} locations defined (recommended: 4-6)")

    quests = data.get("quests", [])
    if len(quests) < 2:
        warnings.append(f"Only {len(quests)} quests defined")

    story_arcs = data.get("story_arcs", [])
    if len(story_arcs) < 1:
        warnings.append("No story arcs defined")

    return warnings


def campaign_to_markdown(data: Dict[str, Any]) -> str:
    """Convert campaign data to Obsidian-compatible markdown."""
    campaign = data.get("campaign", {})

    lines = [
        f"# {campaign.get('name', 'Unnamed Campaign')}",
        "",
        f"tags: [campaign, {campaign.get('theme', '').lower().replace(' ', '-') if campaign.get('theme') else 'fantasy'}]",
        "",
        f"Created: {time.strftime('%Y-%m-%d')}",
        f"Levels: {campaign.get('level_range', '1-5')}",
        f"Estimated Sessions: {campaign.get('estimated_sessions', '8-12')}",
        "",
        f"## Overview",
        "",
        campaign.get("description", ""),
        "",
    ]

    # Factions
    factions = data.get("factions", [])
    if factions:
        lines.extend([
            "## Factions",
            "",
            f"### [[{campaign.get('name', 'Campaign')}]/Factions]",
            "",
        ])
        for f in factions:
            lines.extend([
                f"- **{f['name']}** ({f.get('alignment', '???')}) — {f.get('description', '')}",
                f"  - Goals: {', '.join(f.get('goals', []))}",
                f"  - Strength: {f.get('strength', 'unknown')}",
                "",
            ])

    # NPCs
    npcs = data.get("npcs", [])
    if npcs:
        lines.extend([
            "## NPCs",
            "",
            f"### [[{campaign.get('name', 'Campaign')}/NPCs]]",
            "",
        ])
        for npc in npcs:
            lines.extend([
                f"- **[[{campaign.get('name', 'Campaign')}/NPCs/{npc['name']}]]** — {npc.get('role', 'unknown')}"
                + f" ({npc.get('alignment', '??')})"
                + f" — {npc.get('description', '')[:100]}"
                + ("..." if len(npc.get('description', '')) > 100 else ""),
            ])
        lines.append("")

    # Locations
    locations = data.get("locations", [])
    if locations:
        lines.extend([
            "## Locations",
            "",
            f"### [[{campaign.get('name', 'Campaign')}/Locations]]",
            "",
        ])
        for loc in locations:
            map_note = f"[[{campaign.get('name', 'Campaign')}/Maps/{loc['name']}]]" if loc.get("map_style") else ""
            lines.extend([
                f"- **[[{campaign.get('name', 'Campaign')}/Locations/{loc['name']}]]** — {loc.get('type', 'unknown')}"
                + f" (Act {loc.get('act', '?')})"
                + f" — {loc.get('description', '')[:80]}"
                + ("..." if len(loc.get('description', '')) > 80 else ""),
                f"  🗺️ {map_note}" if map_note else "",
            ])
        lines.append("")

    # Quests
    quests = data.get("quests", [])
    if quests:
        lines.extend([
            "## Quests",
            "",
            f"### [[{campaign.get('name', 'Campaign')}/Quests]]",
            "",
        ])
        for q in quests:
            lines.extend([
                f"- **[[{campaign.get('name', 'Campaign')}/Quests/{q['title']}]]** — Act {q.get('act', '?')}"
                + f" [{q.get('type', 'side')}]: {q.get('description', '')[:100]}..."
            ])
        lines.append("")

    # Story Arcs
    story_arcs = data.get("story_arcs", [])
    if story_arcs:
        lines.extend([
            "## Story Arcs",
            "",
        ])
        for arc in story_arcs:
            lines.extend([
                f"### Act {arc.get('act', '?')}: {arc.get('title', 'Unnamed Act')}",
                "",
                arc.get("description", ""),
                "",
                "**Milestones:**",
                "",
            ])
            for ms in arc.get("milestones", []):
                lines.append(f"- {ms.get('name', '')}: {ms.get('description', '')}")
            lines.append("")
            if arc.get("climax"):
                lines.extend([f"**Climax:** {arc['climax']}", ""])
            if arc.get("transition_to_act2"):
                lines.extend([f"**→** {arc['transition_to_act2']}", ""])

    # Artifacts
    artifacts = data.get("artifacts", [])
    if artifacts:
        lines.extend([
            "## Artifacts & McGuffins",
            "",
        ])
        for art in artifacts:
            lines.extend([
                f"### {art.get('name', 'Unnamed Artifact')} [{art.get('type', 'common')}]",
                "",
                art.get("description", ""),
                "",
            ])
            if art.get("fragments"):
                lines.append(f"**Fragments:** {art['fragments']}")
                for i, power in enumerate(art.get("fragment_powers", [])):
                    lines.append(f"- Fragment {i+1}: {power}")
                lines.append("")

    return "\n".join(lines)


def build_npc_markdown(campaign_name: str, npc: Dict) -> str:
    """Build individual NPC note content for Obsidian."""
    lines = [
        f"# [[{campaign_name}]]/{npc.get('name', 'Unknown NPC')}",
        "",
        f"tags: [npc, {npc.get('role', 'unknown')}]",
        "",
        f"Role: {npc.get('role', 'unknown')}",
        f"Faction: {npc.get('faction', 'None')}",
        f"Alignment: {npc.get('alignment', '??')}",
        "",
        f"## Description",
        "",
        npc.get("description", ""),
        "",
        f"## Personality",
        "",
        " ".join(f"- {t}" for t in npc.get("personality", ["mysterious"])),
        "",
        f"## Motivations",
        "",
        " ".join(f"- {m}" for m in npc.get("motivations", ["unknown"])),
        "",
        f"## Relationships",
        "",
        " ".join(f"- {r}" for r in npc.get("relationships", ["neutral to all"])),
        "",
        f"## Stat Block",
        "",
        npc.get("stat_block", "TBD"),
        "",
        f"## First Appearance",
        "",
        npc.get("first_appearance", "TBD"),
        "",
    ]
    return "\n".join(lines)


def build_location_markdown(campaign_name: str, loc: Dict) -> str:
    """Build individual location note content for Obsidian."""
    lines = [
        f"# [[{campaign_name}]]/{loc.get('name', 'Unknown Location')}",
        "",
        f"tags: [location, {loc.get('type', 'unknown')}]",
        "",
        f"Type: {loc.get('type', 'unknown')}",
        f"Act: {loc.get('act', '?')}",
        "",
        f"## Description",
        "",
        loc.get("description", ""),
        "",
        f"## Key Features",
        "",
        " ".join(f"- {f}" for f in loc.get("key_features", [])),
        "",
        f"## Connections",
        "",
        " ".join(f"- {c}" for c in loc.get("connections", [])),
        "",
    ]

    if loc.get("map_style"):
        lines.extend([
            f"## Map",
            "",
            f"Map style: {loc['map_style']}",
            f"Map file: `maps/{loc.get('name', '').lower().replace(' ', '_')}_map.png`",
            "",
        ])

    return "\n".join(lines)


def build_quest_markdown(campaign_name: str, quest: Dict) -> str:
    """Build individual quest note content for Obsidian."""
    lines = [
        f"# [[{campaign_name}]]/Quests/{quest.get('title', 'Unknown Quest')}",
        "",
        f"tags: [quest, {quest.get('type', 'side')}]",
        "",
        f"Type: {quest.get('type', 'side')}",
        f"Act: {quest.get('act', '?')}",
        f"Status: [[active|not-started]]",
        "",
        f"## Description",
        "",
        quest.get("description", ""),
        "",
        f"## Objectives",
        "",
    ]
    for i, obj in enumerate(quest.get("objectives", []), 1):
        lines.append(f"{i}. {obj.get('desc', '')}" + (f" — Check: {obj.get('check', '')}" if obj.get('check') else ""))
    lines.append("")

    if quest.get("rewards"):
        lines.extend([
            "## Rewards",
            "",
            " ".join(f"- {r}" for r in quest.get("rewards", [])),
            "",
        ])

    if quest.get("consequences"):
        lines.extend([
            "## Consequences",
            "",
            f"- **Success:** {quest['consequences'].get('success', '')}",
            f"- **Failure:** {quest['consequences'].get('failure', '')}",
            "",
        ])

    prereq = quest.get("prerequisite")
    leads_to = quest.get("leads_to")
    if prereq or leads_to:
        lines.extend([
            "## Dependencies",
            "",
            f"- Prerequisite: {prereq}" if prereq else "",
            f"- Leads to: {leads_to}" if leads_to else "",
            "",
        ])

    return "\n".join(lines)
