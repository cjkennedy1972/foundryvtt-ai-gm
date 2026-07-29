"""
Campaign Generator — LLM-driven campaign structure generation for FoundryVTT.

Generates complete FoundryVTT campaign data including:
- Campaign metadata (name, theme, tone, level_range)
- NPCs with stat blocks, motivations, relationships
- Locations with descriptions, map references, connections
- Scenes (combat rooms, exploration areas)
- Journal entries and quest logs
- Quests with objectives, rewards, story progression
- Loot tables and magical items
- Story arcs with acts, milestones, major events
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# System prompt for campaign generation — forces structured JSON output
CAMPAIGN_GENERATOR_PROMPT = """You are a Campaign Architect for a TTRPG GM system built on FoundryVTT. Your job is to design complete, playable campaigns that run entirely within FoundryVTT.

## How You Respond

You respond with a SINGLE JSON object containing the full campaign structure.

```json
{
  "campaign": {
    "name": "The Shattered Crown",
    "theme": "Political intrigue, ancient magic, redemption",
    "tone": "Dark fantasy with glimmers of hope",
    "level_range": "1-5",
    "estimated_sessions": "12-16",
    "description": "A paragraph describing the campaign premise...",
    "setting_type": "medieval-fantasy",
    "pantheon": "Polytheistic pantheon with 5 major deities",
    "magic_level": "standard"
  },
  "scenes": [
    {
      "name": "The Gilded Tavern — Main Hall",
      "type": "settlement",
      "act": 1,
      "description": "A bustling two-story tavern. The main hall has a raised dais with the bar, wooden tables, and a crackling fireplace.",
      "map_needed": true,
      "map_style": "top-down dungeon map, two-story tavern interior, warm torchlight, wooden floorboards, bar counter at back, tables scattered around, cozy atmosphere, parchment style",
      "map_scale": "room-scale",
      "token_count": 6,
      "lighting": "warm torchlight, fireplace glow",
      "atmosphere": "bustling, lively, smells of roasted meat and ale",
      "scene_setup": {
        "grid_width": 16,
        "grid_height": 12,
        "grid_size_px": 64,
        "fog_exploration": false,
        "token_vision": false,
        "global_illumination": true,
        "darkness": 0.0,
        "walls": [
          [0,0,16,0],
          [16,0,16,12],
          [16,12,0,12],
          [0,12,0,0],
          [0,5,6,5],
          [6,5,6,12],
          [10,0,10,5]
        ],
        "doors": [
          {
            "c": [7, 12, 9, 12],
            "door": 1,
            "ds": 0
          }
        ],
        "lights": [
          {
            "x": 3,
            "y": 9,
            "bright": 15,
            "dim": 25,
            "color": "#ff6600",
            "alpha": 0.6
          },
          {
            "x": 13,
            "y": 3,
            "bright": 10,
            "dim": 20,
            "color": "#ffaa44",
            "alpha": 0.5
          }
        ],
        "sounds": [
          {
            "x": 8,
            "y": 6,
            "path": "sounds/tavern-ambience.ogg",
            "radius": 20,
            "volume": 0.3
          }
        ]
      }
    }
    // ↑ ONE scenes shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
  ],
  "journal_entries": [
    {
      "title": "The Shattered Crown — Prophecy",
      "body": "From the ruins of the old kingdom, three heroes shall rise to reclaim the Shattered Crown. But beware — for every fragment reclaimed brings the fallen god's wrath upon the land.",
      "type": "prophecy",
      "act": 1,
      "visible_to_players": true
    }
    // ↑ ONE journal_entries shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
  ],
  "quest_logs": [
    {
      "id": "quest_1",
      "title": "The Cursed Well",
      "type": "main",
      "act": 1,
      "description": "The village well has begun producing black water that drives livestock mad.",
      "objectives": [
        {
          "desc": "Investigate the well at night",
          "check": "Perception DC 14"
        },
        {
          "desc": "Discover the corrupted spirit of the old water nymph",
          "check": "Arcana DC 16"
        },
        {
          "desc": "Purge the corruption with a ritual",
          "check": "Religion DC 15"
        }
      ],
      "rewards": ["50 gold pieces", "Village gratitude (+1 reputation)", "Ancient water rune artifact"],
      "consequences": {
        "success": "Village is safe, NPC gains trust",
        "failure": "Corruption spreads, NPCs turn hostile"
      },
      "status": "not-started",
      "assigned_actors": []
    }
    // ↑ ONE quest_logs shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
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
      "stat_block": "CR 2 — ancient enchantress, powerful in nature magic, AC 14 (staff), HP 45, attacks: entangle cantrip, entangle (DC 14), barkskin buff",
      "portrait_needed": true,
      "first_appearance": "Act 1, Scene 1 — the village gathering"
    }
    // ↑ ONE npcs shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
  ],
  "locations": [
    {
      "name": "Riverbend Village",
      "type": "village",
      "act": 1,
      "description": "A small farming village on the banks of the Silverstream.",
      "key_features": ["Old mill", "Village elder's house", "Cursed well"],
      "connections": ["Forest path to Ruins of Valdor (2h)", "Road to Oakhaven (half day)"],
      "rumors": ["Caravans from Oakhaven stopped arriving after the Obsidian Hand bought the smelter there"],
      "map_needed": true,
      "map_style": "top-down village overview, misty morning, stone cottages, river, old mill, purple overcast sky, ancient towering ancient stone trees, wide cinematic view",
      "scenes": ["The Gilded Tavern — Main Hall", "Morwenna's Herb Shop", "The Cursed Well"]
    }
    // ↑ ONE location shown for SHAPE ONLY (a campaign site: real `act` + `scenes`).
    //   Produce as many as the count checklist requires — populate the WHOLE world,
    //   not just the plot. Most locations are world-only: surrounding towns, regions,
    //   and landmarks the party may never visit. Those use `"act": null` and
    //   `"scenes": []` (e.g. "Oakhaven", a trade town half a day down the road that
    //   merchants and rumors come from), but still get `type`, `description`,
    //   `key_features`, `connections`, and a `map_style`.
    //   MAKE WORLD-ONLY LOCATIONS EARN THEIR PLACE — each should be pulled into the
    //   campaign by at least one of: a `rumors` hook (short lead tying it to the plot
    //   or a faction), a side quest whose `location` names it, a faction `goals`
    //   entry, or an artifact `current_locations` entry. `rumors` is a list of 1-2
    //   short in-world leads a GM can drop to send players there. (Imported campaigns:
    //   draw rumors and off-plot quest sites from the source notes, never invent them.)
  ],
  "loot_tables": [
    {
      "name": "Cursed Well Rewards",
      "description": "Loot found in and around the Cursed Well area.",
      "table_type": "treasure",
      "entries": [
        {
          "name": "Water Rune of Purification",
          "type": "wondrous_item",
          "rarity": "uncommon",
          "weight": 30,
          "description": "A smooth river stone inscribed with glowing blue runes. Once per day, you can purify contaminated water.",
          "quantity": 1
        },
        {
          "name": "Potions of Healing (2x)",
          "type": "consumable",
          "rarity": "common",
          "weight": 40,
          "description": "Standard potions of healing.",
          "quantity": 2
        },
        {
          "name": "Gold Coins (30d10)",
          "type": "currency",
          "rarity": "common",
          "weight": 30,
          "description": "Ancient gold coins from the old kingdom.",
          "quantity": 1
        }
      ]
    }
    // ↑ ONE loot_tables shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
  ],
  "factions": [
    {
      "name": "The Obsidian Hand",
      "description": "A secret society of warlocks seeking to overthrow the aristocracy.",
      "alignment": "NE",
      "goals": ["Infiltrate every noble house", "Awaken the fallen god beneath the capital", "Control the Oakhaven smelter to arm their cult"],
      "strength": "moderate",
      "members": 12
    }
    // ↑ ONE factions shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
  ],
  "artifacts": [
    {
      "name": "The Shattered Crown",
      "description": "The broken diadem of the last Elven king. Each fragment grants a different power.",
      "type": "legendary",
      "fragments": 3,
      "fragment_powers": ["Wielder can see through deception", "Wielder commands plant and stone", "Wielder can command the dead"],
      "current_locations": ["ruins act 2", "villain lair act 3", "Oakhaven smelter vault (off-plot — a fragment waits in a world-only location)"]
    }
    // ↑ ONE artifacts shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
  ],
  "encounters": [
    {
      "name": "Ambush at the Flooded Altar",
      "act": 1,
      "linked_scene": "The Sunken Crypt",
      "description": "Undead guardians rise as the party approaches the hidden altar.",
      "trigger": "When players cross the threshold into the sarcophagus chamber (the door at grid 10,5)",
      "difficulty": "medium",
      "environment_notes": "Corridor chokepoint at (10,5) limits simultaneous engagement to 2 creatures. Standing water south of y=10 is difficult terrain. Sarcophagi at (6,7) and (14,7) provide half-cover for ranged attackers. Torches at (4,3) and (17,3) are the only bright-light sources — extinguishing one creates a tactical advantage for darkvision creatures.",
      "monsters": [
        {
          "name": "Skeleton",
          "compendium_search": "Skeleton",
          "cr": 0.25,
          "count": 3,
          "hp": 13,
          "ac": 13,
          "disposition": -1,
          "placement": [
            {
              "grid_x": 15,
              "grid_y": 3
            },
            {
              "grid_x": 17,
              "grid_y": 5
            },
            {
              "grid_x": 13,
              "grid_y": 8
            }
          ]
        },
        {
          "name": "Zombie",
          "compendium_search": "Zombie",
          "cr": 0.25,
          "count": 2,
          "hp": 22,
          "ac": 8,
          "disposition": -1,
          "placement": [
            {
              "grid_x": 10,
              "grid_y": 11
            },
            {
              "grid_x": 14,
              "grid_y": 13
            }
          ]
        }
      ],
      "tactical_notes": "Skeletons use shortbows from behind sarcophagi while zombies advance through the water as a slow front. GM tip: have a zombie emerge from a sarcophagus mid-combat for dramatic effect. The chokepoint at x=10 means only 2 party members can engage melee at once — a classic funnel.",
      "xp_award": 250,
      "rewards": ["50 gold pieces", "Ancient Sarcophagus Blade (see Sunken Crypt Boons loot table)"]
    }
    // ↑ ONE encounters shown for SHAPE ONLY. Produce as many as the count checklist in the user message requires.
  ],
  "prologue": {
    "vessel": "tapestry",
    "title": "The Weave of the Sundered Oath",
    "frame_narrative": "One paragraph: who/what presents this to the players and where.",
    "panels": [
      { "title": "The Age of Concord",
        "body": "2-4 sentences of GM boxed text, past tense, mythic register.",
        "image_prompt": "Scene description WITHOUT style words — the vessel preset supplies style.",
        "era": "ancient" }
    ]
  }
}
```

### Prologue / Illustrated Campaign Introduction (OPTIONAL)

The **prologue** is an additive, build-time–generated artifact. If the key is absent, the campaign behaves exactly as before. When present, it becomes an illustrated JournalEntry presented at session start (Phase C).

**Vessel** — the presentation form that determines art style, frame narrative, and narration register. Choose ONE from:
| Vessel | Art style preset | Frame narrative example |
|---|---|---|
| `tome` | illuminated manuscript, gold leaf, aged vellum | "A monk opens the Chronicle of the Age..." |
| `scroll` | faded ink on cracked parchment, sepia | "The herald unrolls a scroll sealed a century ago..." |
| `gallery` | oil paintings, ornate gilt frames, chiaroscuro | "You pass through the great hall; the portraits watch..." |
| `tapestry` | woven textile art, medieval Bayeux style | "Firelight moves across the threadbare tapestry..." |
| `stained_glass` | stained glass window, lead lines, luminous color | "Dawn ignites the cathedral windows one by one..." |
| `mural` | weathered fresco, cracked plaster, faded pigment | "Your torch reveals paintings older than the kingdom..." |
| `cartographer` | antique map, ink annotations, compass roses | "The old explorer spreads his charts across the table..." |

Gothic → `stained_glass`; seafaring → `cartographer`; war epic → `tapestry`. The LLM may invent a variant within the schema.

**Panels** — 5–7 panels forming an arc-shaped story:
*world before → the wound/catastrophe → powers that rose → present tension → "where you stand"*
The last panel MUST land on the party's starting location (bridges into Act 1).

Each panel object:
```json
{
  "title": "The Age of Concord",
  "body": "2-4 sentences of GM boxed text, past tense, mythic register",
  "image_prompt": "scene description WITHOUT style words — the vessel preset supplies style",
  "era": "ancient | mythic | historical | recent | present"
}
```

**Validation (additive):** When `prologue` key exists, it requires `vessel` + at least 4 `panels`. Legacy campaigns without the key pass validation untouched.

## Campaign Design Principles

1. **Structure**: 3-5 acts with clear progression. Each act has a beginning, middle, climax, and hook for the next act.
2. **NPCs**: Each NPC should have clear motivations, personality traits, and relationships. They should feel alive, not quest dispensers.
3. **Locations**: Each location should have a distinct visual identity, key features, and connections. Include map style hints. Populate the world beyond the plot: alongside the sites the campaign visits, add surrounding settlements, regions, and landmarks the party may never enter so the map feels like a real, lived-in world. Tie world-only locations to the setting with `connections`, set `act: null`, and leave `scenes: []`.
4. **Quests**: Quests should have clear objectives, interesting challenges (not just combat), meaningful rewards, and consequences.
5. **Story Arcs**: Build thematic arcs — personal (character growth), political (faction struggles), and cosmic (larger threats).
6. **Pacing**: Mix quiet moments with action. Give players room to make choices that matter.
7. **Player Agency**: Design hooks that lead to multiple possible paths. Never railroad.
8. **Hooks for Customization**: Include 2-3 "insert your players here" moments where the GM can adapt to their party's composition.
9. **Encounters**: Every act needs at least one combat encounter. Scale CR to the party's level range. Use the scene's `scene_setup` grid and walls to place monsters tactically — never inside wall segments. Each encounter must reference an existing scene by exact name.

## Map Style Guidelines

When generating `map_style` hints for locations and scenes, be VERY SPECIFIC and VISUAL. Include:

### Structure
Start with **perspective + type**: "top-down dungeon map" | "isometric village" | "aerial overworld" | "first-person interior" | "side-view cavern"

### Visual Details (most important for image quality)
Include 4-6 specific visual elements:
- **Focal points**: tavern bar, merchant stalls, throne, altar, fountain, statue, giant doors, barricades
- **Terrain/Materials**: stone floors, wooden beams, dirt roads, cobblestones, moss-covered walls, sand, water features
- **Decorative details**: tapestries, murals, torches, candles, barrels, crates, rubble, bones, treasure piles, vines
- **Natural features**: trees, mountains, cliffs, rivers, waterfalls, lakes, forests, clearings

### Lighting & Mood
- **Time of day color palette**: "golden dawn light", "harsh noon sun", "cool dusk shadows", "deep twilight blue", "torch-orange firelight", "cold moonlight"
- **Atmospheric effects**: "smoky haze", "fog rolling in", "dust motes in shafts of light", "glowing runes", "magical luminescence", "eerie shadows"

### Artistic Style
Add ONE artistic direction: "medieval cartography style", "parchment texture", "watercolor painting", "fantasy illustration", "gritty oil painting", "detailed engraving"

### Example Templates

**Tavern (room-scale, interior)**
"top-down tavern interior, wooden floorboards, bar counter with bottles, round tables with chairs, fireplace with warm glow, wooden beams overhead, hanging lanterns, cozy ale-house atmosphere, medieval cartography style"

**Dungeon (room-scale, combat)**
"top-down dungeon map, stone corridors with columns, arched ceiling, torches in wall sconces, bones scattered on floor, standing water puddles, worn tapestries, oppressive dark atmosphere, gritty parchment texture, dramatic shadows"

**Village (exploration-scale)**
"isometric village overview, thatched-roof cottages, stone church steeple, muddy streets, market stalls with awnings, wooden fence lines, rolling hills beyond, morning mist, scattered NPCs, warm daylight, fantasy village illustration"

**Crypt (room-scale, eerie)**
"top-down flooded crypt, water knee-deep reflecting torchlight, stone sarcophagi along walls, skeletal remains visible, glowing ancient runes on floor, dripping water echoes, eerie blue spectral glow, oppressive cold darkness, detailed fantasy painting"

**Forest Clearing (exploration-scale)**
"aerial view forest clearing, ancient standing stones in circle, moss-covered boulders, tall dark trees framing edges, wildflowers and ferns, narrow path through center, misty dawn light filtering through canopy, sacred magical atmosphere, watercolor fantasy style"

**Castle Throne Room (dramatic)**
"top-down throne room, high domed ceiling, grand staircase descending, throne on dais, marble pillars, crimson carpet runner, stained glass windows, golden candlelight, royal regalia banners, intimidating and majestic, detailed oil painting"

### Key Tips for Vivid Maps
- Be 2-3x more descriptive than you think necessary
- Use sensory language: "cold stone", "flickering flames", "damp mossy", not just "stone", "fire", "moss"
- Include implied action/danger: "bones scattered", "scorch marks on walls", "overturned furniture"
- Mention color palettes explicitly: "warm amber and deep shadows" or "cool blue moonlit tones"
- Map generation works MUCH better with 8+ specific visual details vs. generic descriptions

## Module Integration Fields

Add these fields when the corresponding modules are listed as active in the prompt.
Every field here maps directly to a Foundry flag or system property that the builder deploys.

### NPCs — full schema

```json
{
  "animation_type": "melee",
  "npc_type": "combat",
  "cr": 3,
  "ac": 15,
  "ac_source": "chain mail",
  "hp": 52,
  "hp_formula": "8d8+16",
  "speed": 30,
  "senses": {"darkvision": 60, "blindsight": 0, "tremorsense": 0, "truesight": 0},
  "languages": ["common", "elvish"],
  "damage_resistances": ["cold", "necrotic"],
  "damage_immunities": ["poison"],
  "damage_vulnerabilities": ["fire"],
  "condition_immunities": ["charmed", "frightened", "poisoned"],
  "weapon_items": ["Longsword", "Hand Crossbow"],
  "spells": [
    {"name": "Fire Bolt", "level": 0, "school": "evocation", "damage": "2d10", "damage_type": "fire", "range": 120, "concentration": false, "save": null, "aoe": null},
    {"name": "Shield", "level": 1, "school": "abjuration", "damage": null, "damage_type": null, "range": 0, "concentration": false, "save": null, "aoe": null},
    {"name": "Fireball", "level": 3, "school": "evocation", "damage": "8d6", "damage_type": "fire", "range": 150, "concentration": false, "save": "dex", "save_dc": 14, "aoe": {"type": "sphere", "size": 20}}
  ],
  "active_effects": [
    {"label": "Undead Fortitude", "icon": "icons/svg/skull.svg", "description": "Drops to 1 HP instead of 0 on a hit unless the damage is radiant or a critical hit."}
  ],
  "concentration_caster": false,
  "critical_threshold": 20,
  "patrol_route": ["North Gate", "East Rampart", "South Gate"],
  "gm_token_note": "Secretly working for the Obsidian Hand. Will betray the party in Act 2.",
  "language_spoken": "elvish"
}
```

- `animation_type`: `"melee"` | `"ranged"` | `"magic"` | `"undead"` | `"beast"` | `"divine"` | `"none"`
- `npc_type`: `"combat"` | `"merchant"` | `"guard"` | `"civilian"` | `"boss"`
- `weapon_items`: official D&D 5e item names — used by Automated Animations for JB2A matching (e.g. `"Longsword"`, `"Fire Bolt"`, `"Eldritch Blast"`)
- `spells[].save`: ability abbreviation (`"dex"`, `"con"`, `"wis"`, etc.) or `null`
- `spells[].aoe.type`: `"sphere"` | `"cone"` | `"line"` | `"cube"`
- `patrol_route`: list of waypoint location names — guards only
- `gm_token_note`: hidden GM-only note shown on token hover
- `language_spoken`: primary spoken language for Polyglot scrambling in chat
- `damage_resistances` / `damage_immunities` / `damage_vulnerabilities`: D&D 5e damage type names (lower-case): `"acid"`, `"bludgeoning"`, `"cold"`, `"fire"`, `"force"`, `"lightning"`, `"necrotic"`, `"piercing"`, `"poison"`, `"psychic"`, `"radiant"`, `"slashing"`, `"thunder"`
- `condition_immunities`: lower-case condition names: `"blinded"`, `"charmed"`, `"deafened"`, `"exhaustion"`, `"frightened"`, `"grappled"`, `"incapacitated"`, `"invisible"`, `"paralyzed"`, `"petrified"`, `"poisoned"`, `"prone"`, `"restrained"`, `"stunned"`, `"unconscious"`

### Scenes — scene_setup (MANDATORY — EVERY SCENE MUST HAVE THIS)

🔴 **CRITICAL REQUIREMENT**: Every single scene MUST include a `scene_setup` block. If any scene is missing `scene_setup`, the campaign generator will auto-generate a default one, but you should provide explicit setup for best results.

The `scene_setup` block makes scenes immediately playable with walls that block vision, atmospheric lighting, and ambient sounds. Think in **grid squares** — the system converts to pixels automatically (64 pixels per square = fixed constant).

```json
"scene_setup": {
  "grid_width": 20,
  "grid_height": 15,
  "grid_size_px": 64,
  "fog_exploration": true,
  "token_vision": true,
  "global_illumination": false,
  "darkness": 0.8,
  "walls": [
    [0,0,20,0],[20,0,20,15],[20,15,0,15],[0,15,0,0],
    [8,0,8,6],[8,6,14,6],[14,6,14,0]
  ],
  "doors": [
    {"c":[4,0,8,0],"door":1,"ds":0},
    {"c":[16,6,18,6],"door":2,"ds":2}
  ],
  "lights": [
    {"x":4,"y":3,"bright":8,"dim":15,"color":"#ff6600","alpha":0.5},
    {"x":14,"y":10,"bright":12,"dim":22,"color":"#4488ff","alpha":0.6}
  ],
  "sounds": [
    {"x":10,"y":7,"path":"sounds/dungeon-drip.ogg","radius":20,"volume":0.4}
  ],
  "trap_tiles": [
    {"name":"Poison Dart Trap","x":8,"y":6,"w":1,"h":1,"save_ability":"dex","save_dc":13,"damage":"2d6","damage_type":"poison","description":"Darts spring from concealed wall holes."}
  ]
}
```

**Coordinate system (IMPORTANT):** All values are in **grid squares** (NOT pixels).
- `grid_size_px`: ALWAYS `64`. This is fixed — the system generates images at exactly `grid_width × 64` by `grid_height × 64` pixels so walls land on image features.
- `grid_width`/`grid_height`: ONLY use these standard sizes (chosen so pixel dimensions align perfectly with generated images):
  - Small room (tavern, cottage, shop, chamber): `16×12` → 1024×768px image
  - Medium scene (dungeon, settlement, temple, cave): `20×15` → 1280×960px image
  - Large scene (battlefield, wilderness, city, castle): `24×18` → 1536×1152px image
  - Tall indoor (tower, shaft, stairwell, multi-level): `16×20` → 1024×1280px image

  **DO NOT USE**: Other grid sizes (e.g., 17×13, 25×19). The standard sizes ensure pixel-perfect alignment between generated maps and scene canvas.
- `walls`: array of `[x0, y0, x1, y1]` line segments in grid squares. Draw perimeter first, then interior walls.
- `doors`: array of door objects. `door:1`=regular door, `door:2`=secret door. `ds:0`=closed, `ds:2`=locked. `c` is the wall segment endpoint pair in grid squares.
- `lights`: `x`,`y` in grid squares. `bright`/`dim` are light RADIUS in feet (5ft = 1 square). `color`: `#ff6600`=torch, `#4488ff`=arcane, `#ffffff`=daylight, `#00ff88`=nature magic.
- `sounds`: `x`,`y` in grid squares, `radius` in squares.
- `trap_tiles` (OPTIONAL — only for scenes that contain a trap): array of `{name, x, y, w, h, save_ability, save_dc, damage, damage_type, description}`. `x`,`y`,`w`,`h` in grid squares mark the trigger area. Each auto-deploys as an invisible Monk's Active Tile that whispers the trap to the GM when a token enters — the AI GM then calls for the save and applies damage. Only add this when the scene actually has a trap; omit otherwise.

**Scene type guidelines:**
- Tavern/Inn: `fog_exploration:false`, `global_illumination:true`, `darkness:0`, warm orange lights, sound `tavern-ambience.ogg`
- Dungeon/Crypt: `fog_exploration:true`, `token_vision:true`, `darkness:0.7–0.9`, dim torch lights, `dungeon-drip.ogg`
- Forest/Wilderness: `fog_exploration:true`, `darkness:0.3–0.6`, dappled green/white lights, nature sounds
- City street: `fog_exploration:false`, `darkness:0.1`, lantern lights at intersections
- Cave/Underground: `fog_exploration:true`, `darkness:0.85`, minimal lights, `cave-ambience.ogg`
- Temple/Sacred: `token_vision:true`, `darkness:0.4`, blue/purple divine lights

**Wall placement rules:**
1. Always draw the full perimeter (4 walls for a rectangle)
2. Add interior walls for room dividers, pillars, furniture blocking LOS
3. Don't draw walls where corridors connect rooms — leave gaps
4. Place doors on the gap segments (not on existing wall segments)

### Scenes — module flags

```json
{
  "soundscape": "dungeon",
  "darkness": 0.7,
  "fog_exploration": true,
  "global_illumination": false,
  "module_flags": {
    "smalltime": {
      "time_of_day": 22,
      "time_period": "night"
    },
    "dynamic-soundscapes": {
      "soundscape": "dungeon",
      "volume": 0.6
    },
    "levels": {
      "active_level": 0,
      "has_multiple_floors": true,
      "floors": [
        {"name": "Ground Floor", "elevation": 0},
        {"name": "Upper Floor", "elevation": 5}
      ]
    },
    "fog-weaver": {
      "fog_type": "light_fog",
      "fog_density": 0.2
    },
    "betterroofs": {
      "has_roof": true
    }
  }
}
```

**Core Scene Configuration**:
- `soundscape`: `"tavern"` | `"dungeon"` | `"forest"` | `"cave"` | `"combat"` | `"city"` | `"ocean"` | `"crypt"` | `"temple"` | `"wilderness"` | `"market"` | `"throne_room"` | `"none"`
- `darkness`: 0.0 (bright daylight) → 1.0 (pitch black). Use 0.0–0.2 for outdoor day, 0.5–0.7 for torchlit interior, 0.8–1.0 for pitch-black dungeon
- `fog_exploration`: `true` for fog of war exploration, `false` for full reveal
- `global_illumination`: `true` for fully lit scene, `false` for darker atmosphere

**Module-Specific Configuration** (in `module_flags`):

| Module | Flag | Purpose |
|--------|------|---------|
| **smalltime** | `time_of_day` (0-23), `time_period` | Real-time clock and day/night cycle |
| **dynamic-soundscapes** | `soundscape`, `volume` (0-1) | Ambient audio system |
| **levels** | `active_level`, `has_multiple_floors`, `floors[]` | Multi-floor scenes with elevation |
| **fog-weaver** | `fog_type`, `fog_density` (0-1) | Advanced fog of war effects |
| **betterroofs** | `has_roof` | Roof visibility (hide tokens from above) |
| **smalltime + dynamic-soundscapes** | Both combined | Time-aware ambient soundscapes |

**How to populate module_flags**:

1. Check which modules are ACTIVE in the deployment environment
2. For each active module, add corresponding configuration to `module_flags`
3. Use module-specific fields to customize the scene experience:
   - `smalltime`: Set time_of_day (0-23) based on scene purpose. Taverns at evening (17-20), crypts in eternal darkness (0), combat encounters at dawn (5-7)
   - `dynamic-soundscapes`: Match soundscape to scene type. Use "dungeon" for crypts, "tavern" for inns, "forest" for wilderness, "combat" for battle locations
   - `levels`: Create multiple floors for towers, castles, multi-level dungeons. Set elevation values for vertical positioning
   - `fog-weaver`: Use fog for mystery, exploration, or atmospheric effects. light_fog for partial obscurity, mystical for magical areas
   - `betterroofs`: Enable for buildings, caves, enclosed structures. Disable for outdoor locations

**Example Module Flag Population**:

Tavern scene at evening:
```json
"module_flags": {
  "smalltime": {"time_of_day": 18, "time_period": "evening"},
  "dynamic-soundscapes": {"soundscape": "tavern", "volume": 0.6}
}
```

Underground dungeon:
```json
"module_flags": {
  "smalltime": {"time_of_day": 0, "time_period": "midnight"},
  "dynamic-soundscapes": {"soundscape": "dungeon", "volume": 0.7},
  "fog-weaver": {"fog_type": "light_fog", "fog_density": 0.3},
  "betterroofs": {"has_roof": true}
}
```

Multi-floor castle:
```json
"module_flags": {
  "levels": {
    "has_multiple_floors": true,
    "active_level": 0,
    "floors": [
      {"name": "Ground Floor", "elevation": 0},
      {"name": "First Floor", "elevation": 5},
      {"name": "Ramparts", "elevation": 10}
    ]
  },
  "betterroofs": {"has_roof": false}
}
```

### Loot tables — full schema

```json
{
  "pile_type": "chest",
  "deploy_as_pile": true,
  "entries": [
    {
      "name": "Flame Tongue Longsword",
      "weight": 20,
      "quantity": 1,
      "rarity": "rare",
      "foundry_item_type": "weapon",
      "value_gp": 5000,
      "weight_lbs": 3.0,
      "description": "A sword that bursts into flame on command dealing an extra 2d6 fire damage."
    }
  ]
}
```

### Journal entries — language

```json
{
  "language": "elvish"
}
```

- `language`: D&D 5e language key — text appears scrambled to players who don't speak it
- Values: `"common"` | `"elvish"` | `"dwarvish"` | `"infernal"` | `"draconic"` | `"orcish"` | `"abyssal"` | `"celestial"` | `"primordial"` | `"sylvan"` | `"deep-speech"` | `"undercommon"` | `"gnomish"` | `"halfling"`
- Only set for in-world texts (ancient tomes, cryptic messages, cultist documents). Use `null` for player-facing English text.

### Quest logs — enhanced schema

```json
{
  "quest_giver": "Elder Morwenna",
  "location": "Oakhaven",
  "difficulty": "medium",
  "xp_reward": 300,
  "time_limit_days": 7,
  "calendar_due_date": {"year": 1, "month": 3, "day": 22}
}
```

- `location`: the place the quest sends the party — MAY be any location, including a
  world-only one (`act: null`). Site at least one side quest off the main plot so the
  extra world locations become reasons to explore, not just map decoration. (Imported
  campaigns: only site quests where the source notes support it.)

### Top-level arrays

```json
"playlists": [
  {
    "name": "Dungeon Ambience",
    "mood": "tense dripping water, distant rumbling, the echo of footsteps",
    "scene": "The Sunken Crypt",
    "loop": true
  }
],
"calendar_events": [
  {
    "title": "Festival of the Harvest Moon",
    "year": 1, "month": 9, "day": 21,
    "description": "Annual festival where a magical convergence amplifies all spells cast.",
    "type": "festival",
    "visible_to_players": true
  },
  {
    "title": "[GM] Enemy Siege Begins",
    "year": 1, "month": 10, "day": 1,
    "description": "Enemy forces reach the capital walls if the players haven't intervened.",
    "type": "plot_deadline",
    "visible_to_players": false
  }
]
```

### Encounters — full schema and placement rules

Every `encounters` entry must link to an existing scene by its exact `name`. Use `scene_setup.walls`
(grid-square line segments `[x0,y0,x1,y1]`) to reason about room geometry BEFORE placing monsters:

- **Do not place tokens inside or directly on wall segments** — they block movement and vision.
- Use wall chokepoints (narrow corridors, single-square doors) for tactical funnel encounters.
- Spread monsters across the playable area; use cover objects (sarcophagi, pillars, barrels) noted in `environment_notes`.
- `placement` grid coords are the TOKEN'S grid square (top-left corner). Stay within `grid_width` × `grid_height`.
- `compendium_search` should be a standard D&D 5e Monster Manual name (e.g. "Skeleton", "Goblin", "Bandit").
- `disposition`: -1 = hostile (red nameplate), 0 = neutral, 1 = friendly.
- `xp_award` follows D&D 5e XP-per-CR tables: CR 1/4=50, 1/2=100, 1=200, 2=450, 3=700, 4=1100, 5=1800.

```json
{
  "name": "Goblin Ambush in the Ravine",
  "act": 1,
  "linked_scene": "The Sunken Crypt",
  "description": "Goblins have taken up ambush positions — archers on the high ledge, melee fighters in the center.",
  "trigger": "When players enter the scene past grid x=4",
  "difficulty": "easy",
  "environment_notes": "Wall segments define two flanking corridors. Crates at (3,6) and (9,6) give half-cover. The south wall at y=12 has no door — it is a dead end that can trap players if retreat is needed. Bright light from the opening at x=0 silhouettes approaching PCs.",
  "monsters": [
    {
      "name": "Goblin",
      "compendium_search": "Goblin",
      "cr": 0.25,
      "count": 4,
      "hp": 7,
      "ac": 15,
      "disposition": -1,
      "placement": [
        {"grid_x": 10, "grid_y": 3},
        {"grid_x": 12, "grid_y": 5},
        {"grid_x": 8, "grid_y": 8},
        {"grid_x": 14, "grid_y": 9}
      ]
    },
    {
      "name": "Goblin Boss",
      "compendium_search": "Goblin Boss",
      "cr": 1,
      "count": 1,
      "hp": 21,
      "ac": 17,
      "disposition": -1,
      "placement": [
        {"grid_x": 13, "grid_y": 6}
      ]
    }
  ],
  "tactical_notes": "Goblins use Nimble Escape to disengage after attacking. Boss commands two goblins to redirect attacks to a PC each round. If Boss drops below 10 HP, goblins may flee.",
  "xp_award": 250,
  "rewards": ["Goblin coin pouch (12 gp)", "Crude map showing next dungeon area"]
}
```

## CRITICAL OUTPUT RULES

- **OUTPUT ONLY THE JSON OBJECT.** No thinking, no reasoning, no explanation.
- **Start your response directly with `{`** and end with `}`.
- **Do NOT output any text before or after the JSON.**
- **Be a JSON object from the very first character.**
"""


def _level_scaling(level_range: str) -> dict:
    """Return scene/act/NPC/encounter counts scaled to a D&D 5e level range.

    Level tiers map to D&D 5e play tiers:
      1-4   Tier 1 — Local Heroes
      5-10  Tier 2 — Heroes of the Realm
      11-16 Tier 3 — Masters of the Realm
      17-20 Tier 4 — Masters of the World

    A full campaign spanning N tiers gets proportionally more content.
    We budget ~3-4 deployable scenes per tier (scenes are expensive to generate
    and are deployed in arcs, not all at once).
    """
    try:
        parts = [int(x.strip()) for x in level_range.replace("–", "-").split("-") if x.strip()]
        lo, hi = (parts[0], parts[-1]) if len(parts) >= 2 else (parts[0], parts[0])
    except (ValueError, IndexError):
        lo, hi = 1, 5

    span = max(hi - lo, 0)

    # Location counts are deliberately higher than the scene/act counts: a
    # generated world should feel populated, so locations cover both the sites
    # the campaign actually visits AND surrounding settlements, regions, and
    # landmarks that fill out the map even if the party never goes there.
    if span <= 5:       # One tier / short arc  (e.g. 1-5, 5-10)
        return dict(acts="2-3", scenes="3-5", npcs="3-5", locations="8-12", encounters="2-4", quests="2-3", arcs="1-2", loot_tables="1-2", factions="1-2", artifacts="1-1")
    elif span <= 10:    # Two tiers / medium campaign  (e.g. 1-10, 3-12)
        return dict(acts="4-6", scenes="5-8", npcs="5-8", locations="12-16", encounters="4-6", quests="3-5", arcs="2-3", loot_tables="2-3", factions="1-2", artifacts="1-2")
    elif span <= 15:    # Three tiers / long campaign  (e.g. 1-15, 3-17)
        return dict(acts="6-9", scenes="8-12", npcs="7-10", locations="16-22", encounters="6-9", quests="5-7", arcs="3-4", loot_tables="3-4", factions="2-3", artifacts="2-3")
    else:               # Four tiers / full epic  (e.g. 1-20)
        return dict(acts="9-12", scenes="10-15", npcs="9-12", locations="22-30", encounters="8-12", quests="6-9", arcs="4-5", loot_tables="4-6", factions="2-4", artifacts="2-4")


def generate_campaign_prompt(user_input: str, active_modules: dict = None, level_range: str = "1-5") -> str:
    """Build the full prompt for the LLM campaign generator.

    Args:
        user_input: The user's campaign description/prompt.
        active_modules: Dict of {module_id: {title, version}} for active Foundry modules.
        level_range: D&D 5e level range string like "3-12". Controls how many
            scenes/acts/encounters the LLM is asked to generate.
    """
    module_block = ""
    if active_modules:
        lines = ["\n## Active FoundryVTT Modules — use these to add the fields described above\n"]
        addon_map = {
            # ── Animation & VFX ──────────────────────────────────────────────
            "autoanimations":        "Automated Animations — REQUIRED: add `animation_type` + `weapon_items` (official D&D 5e names) to every NPC",
            "JB2A_DnD5e":            "JB2A Assets (free) — animation library backing autoanimations; use exact D&D 5e item names for best hit rate",
            "jb2a_patreon":          "JB2A Assets (patreon) — extended animation pack; wider spell/weapon coverage",
            "sequencer":             "Sequencer — animation sequencing engine; pairs with autoanimations; always active when autoanimations is installed",
            # ── Combat & Mechanics ────────────────────────────────────────────
            "midi-qol":              "Midi QOL — REQUIRED: add `spells` array (with `save`, `save_dc`, `damage`, `aoe`) to caster NPCs; add `critical_threshold` and `concentration_caster` to spellcasters",
            "dae":                   "Dynamic Active Effects — add `active_effects` array to NPCs for persistent buffs/debuffs/auras",
            "times-up":              "Times Up — active effects expire by time/round; pair with dae `active_effects` durations",
            "combat-tracker-dock":   "Carousel Combat Tracker — rich combat UI; initiative ties broken by Dex modifier automatically",
            "ready-set-go":          "Ready Set Go — readied action support; no extra fields needed",
            "simbuls-creature-aide": "Simbul's Creature Aid — auto-links NPC damage resistances to system traits; ensure `damage_resistances`, `damage_immunities`, `damage_vulnerabilities` are set",
            "mmm":                   "Maxwell's Maladies — condition overlay tracking; add `condition_immunities` array to NPCs; add `damage_resistances`/`damage_immunities`/`damage_vulnerabilities` for full coverage. Add `conditions` array to NPCs with active status effects.",
            "token-action-hud-core": "Token Action HUD — action bar auto-built from actor; no extra fields needed",
            "token-action-hud-dnd5e":"Token Action HUD D&D 5e — D&D 5e action types shown per token",
            # ── Items, Inventory & Economy ────────────────────────────────────
            "item-piles":            "Item Piles — REQUIRED: set `deploy_as_pile:true` + `pile_type` on loot tables; set `npc_type:'merchant'` on shops; add `value_gp`/`weight_lbs`/`foundry_item_type` to every loot entry",
            "itempilesdnd5e":        "Item Piles D&D 5e — loot items use official D&D 5e price/weight fields (activates automatically)",
            "lootsheet-simple":      "Loot Sheet NPC — alternative loot UI; merchant/loot actors use lootsheet if item-piles unavailable",
            # ── Vision & Lighting ─────────────────────────────────────────────
            "vision-5e":             "Vision 5e — REQUIRED: add `senses` object (darkvision, blindsight, tremorsense, truesight in feet) to every NPC",
            "perfect-vision":        "Perfect Vision — per-token sight modes; vision-5e takes precedence if both active",
            "gm-vision":             "GM Vision — GM sees through darkness; flag hidden/invisible NPCs with `gm_token_note`",
            # ── Scenes & Environment ──────────────────────────────────────────
            "levels":                "Levels (3D multi-floor) — add `has_multiple_floors:true` + `floors` array to multi-story buildings and towers",
            "betterroofs":           "Better Roofs — add `has_roof:true` to indoor/walled scenes; roofs hide tokens from overhead view",
            "fog-weaver":            "Fog Weaver — atmospheric fog overlays; add `fog_type` and `fog_density` to scenes for fog/smoke/mystical effects",
            "smalltime":             "SmallTime — in-world clock display; add `time_of_day` (0-23) and `time_period` to every scene",
            "foundryvtt-simple-calendar-reborn": "Simple Calendar Reborn — in-game calendar; include a `calendar_events` array with festivals, plot deadlines, and seasonal events",
            "weather-fx":            "Weather FX — particle weather effects; pair with `weather` field on scenes",
            "indy-walls":            "Indy Walls — auto-generates walls from scene images; mark scenes with complex geometry",
            "monks-wall-enhancement":"Monk's Wall Enhancement — one-way walls, terrain walls; use for prison bars, arrow slits, portcullises",
            "wall-height":           "Wall Height — walls block vision below/above set heights; pairs with levels",
            # ── Sound & Music ─────────────────────────────────────────────────
            "dynamic-soundscapes":   "Dynamic Soundscapes — REQUIRED: add `soundscape` key to every scene; include a top-level `playlists` array with scene, mood, and loop fields",
            "moulinette-soundboards":"Soundboard by Moulinette — per-scene ambient sound board; include playlists array",
            "fxmaster":              "FX Master — particle FX and weather particles; pairs with scenes having dramatic atmosphere",
            # ── Token & NPC Behavior ──────────────────────────────────────────
            "bossbar":               "Bossbar — dramatic on-screen health bar. Add `boss:true` ONLY to a climactic villain or elite solo enemy (act finale / named boss); never mooks or groups. The AI GM spotlights it during its fight.",
            "patrol":                "Patrol — add `npc_type:'guard'` and `patrol_route` (list of waypoint names) to guard/sentinel NPCs",
            "token-notes":           "Token Notes — REQUIRED: add `gm_token_note` to every NPC with secret info, plot hooks, or hidden motivations",
            "token-mold":            "Token Mold — randomises tokens from name/HP pools; no extra fields needed",
            "token-attacher":        "Token Attacher — attach objects/lights to tokens; no extra fields needed",
            # ── Language & Text ───────────────────────────────────────────────
            "polyglot":              "Polyglot — REQUIRED: add `language` to journal entries that are ancient texts, letters, or foreign documents (elvish, draconic, etc.); add `language_spoken` to NPCs",
            # ── Quest & Narrative ─────────────────────────────────────────────
            "rpgx-quest-log":        "RPG-X Quest Log — add `quest_giver`, `difficulty`, `xp_reward`, `time_limit_days`, and `calendar_due_date` to every quest",
            "progress-tracker":      "Progress Tracker — quest journals get progress/objectives count tracking automatically",
            "journal-improvements":  "Journal Improvements — richer HTML in journal pages; use structured HTML (h2, h3, ul, em) in `body` fields",
            "journalentrylinks":     "Journal Entry Links — auto-hyperlinks between journal entries; ensure NPC/location names in journal body text are exact",
            # ── UI & QoL ─────────────────────────────────────────────────────
            "monks-active-tiles":    "Monk's Active Tiles — interactive trap triggers. For any scene with a trap, add a `trap_tiles` array to its `scene_setup` (see the scene_setup schema); each entry auto-deploys an invisible enter-trigger that alerts the GM to resolve it.",
            "pings":                 "Pings — GM can ping the map to direct players; no extra fields",
            "hide-gm-rolls":         "Hide GM Rolls — GM rolls hidden by default; no extra fields",
            "dice-so-nice":          "Dice So Nice — 3D dice rolling; no extra fields needed",
            "dice-tray":             "Dice Tray — quick dice roller UI; no extra fields needed",
            "popout":                "Popout — detach windows to secondary monitors; no extra fields",
            "lib-wrapper":           "LibWrapper — library; no prompt fields needed",
            "socketlib":             "SocketLib — library; no prompt fields needed",
            "warpgate":              "Warpgate — token summoning/replacement; summon spells can list summon_type:warpgate",
            "illandril-context-menu-showhide": "Context Menu Show/Hide — UI library; no extra fields",
        }
        for mod_id, mod_info in sorted(active_modules.items()):
            hint = addon_map.get(mod_id, mod_info.get("title", mod_id))
            lines.append(f"- **{mod_id}** ({mod_info.get('version', '?')}): {hint}")
        module_block = "\n".join(lines)

    sc = _level_scaling(level_range)

    return f"""You are designing a TTRPG campaign based on this request:

"{user_input}"

Use your creativity to design a complete, playable FoundryVTT campaign. Keep all text fields SHORT (1-2 sentences max). Include:
- A compelling premise and setting
- {sc['npcs']} NPCs with distinct personalities and motivations (brief stat blocks)
- {sc['locations']} locations — enough to populate the world, not just the plot. Include the sites the campaign visits AND surrounding settlements, regions, and landmarks the party may never enter (neighboring towns, a distant capital, wilderness regions, ruins, roads). World-only locations should set `act: null` and an empty `scenes: []`; they exist to make the map feel lived-in.
- {sc['scenes']} Scenes with short descriptions, map prompts, and a `scene_setup` block (walls/lights/sounds/fog)
- 2-3 Journal entries (prophecies, quest notes)
- {sc['quests']} Quest logs with objectives
- 1-2 Loot tables
- {sc['arcs']} story arcs (each arc covers one tier of play — Tier 1 = levels 1-4, Tier 2 = 5-10, etc.)
- 1 faction, 1 artifact
- {sc['encounters']} combat encounters (at least one per act, CR-scaled to party level, each linked to a scene by exact name; place monster tokens using that scene's `scene_setup` grid — avoid wall segments, use cover and chokepoints tactically)

Structure the campaign across {sc['acts']} acts. Design for a group of 3-4 players at levels {level_range}.
{module_block}

{CAMPAIGN_GENERATOR_PROMPT}

## Required counts for THIS campaign

The example above shows exactly ONE item per array — that is a SHAPE TEMPLATE, not a
target count. Do NOT copy its length. This campaign is levels {level_range}. Produce
these array lengths (aim for the middle of each range):
- scenes: {sc['scenes']} items (the example shows 1 — you must produce {sc['scenes']})
- npcs: {sc['npcs']} items
- locations: {sc['locations']} items (populate the whole world — campaign sites PLUS surrounding towns/regions/landmarks; world-only ones use `act: null`, `scenes: []`)
- quest_logs: {sc['quests']} items
- encounters: {sc['encounters']} items
- loot_tables: {sc['loot_tables']} items
- factions: {sc['factions']} items
- artifacts: {sc['artifacts']} items
- story_arcs: {sc['arcs']} items across {sc['acts']} acts

Before you emit the closing brace, count each array. If any array is shorter than the
minimum above, keep generating items until it meets the count. Undershooting is a failure.
"""


def campaign_count_checklist(level_range: str = "1-5") -> str:
    """Authoritative count checklist for the campaign.

    Rendered into the LAST message the model reads (the user turn) so the numeric
    targets win on recency over the shape-template example buried in the system
    prompt. Small quantized models anchor hard on the concrete example; putting a
    terse hard-number checklist last is the single most effective counter.
    """
    sc = _level_scaling(level_range)
    return (
        "REQUIRED ARRAY COUNTS (this is the authoritative target — the example in the "
        f"system prompt shows only 1 of each for shape). Levels {level_range}:\n"
        f"  scenes={sc['scenes']}, npcs={sc['npcs']}, locations={sc['locations']}, "
        f"quest_logs={sc['quests']}, encounters={sc['encounters']}, "
        f"loot_tables={sc['loot_tables']}, factions={sc['factions']}, artifacts={sc['artifacts']}, "
        f"story_arcs={sc['arcs']} across {sc['acts']} acts.\n"
        "  locations: populate the WHOLE world — include the campaign's sites plus surrounding "
        "towns, regions, and landmarks the party may never visit (world-only ones use act:null, scenes:[]).\n"
        "  prologue: if present, vessel + 5-7 panels (arc-shaped: world before → wound/catastrophe → powers rose → present tension → party start).\n"
        "Count every array before closing the JSON. Do not stop early. "
        "Producing only 1-2 items per array is the most common failure — avoid it."
    )



def _lore_block(lore_context: str) -> str:
    """Format the lore injection block for the arc-extension prompt.

    Returns an empty string when no lore is available, keeping the default
    prompt byte-identical to pre-lore-injection output.
    """
    if not lore_context:
        return ""
    return (
        "## Established World Lore (STAY CONSISTENT — do not contradict these facts)\n\n"
        f"{lore_context}\n\n"
    )


def generate_arc_extension_prompt(
    campaign_data: dict,
    current_level: int,
    arc_number: int,
    active_modules: dict = None,
    lore_context: str = "",
) -> str:
    """Build the LLM prompt for extending an existing campaign with a new arc.

    Produces a JSON blob that has the same schema as a full campaign but only
    contains the *new* content (scenes, encounters, NPCs, quests) for the next
    tier of play.  The caller merges this into the existing campaign document.

    Args:
        campaign_data: The existing campaign dict (used to extract context).
        current_level: Party's current level (the arc starts here).
        arc_number: Which arc number this is (1-based; Arc 1 was the initial build).
        active_modules: Active Foundry module hints.
    """
    camp = campaign_data.get("campaign", {})
    existing_scenes = [s.get("name", "") for s in campaign_data.get("scenes", [])]
    existing_npcs   = [n.get("name", "") for n in campaign_data.get("npcs", [])]
    existing_quests = [q.get("name", "") for q in campaign_data.get("quest_logs", [])]
    existing_arcs   = [a.get("name", "") for a in campaign_data.get("story_arcs", [])]

    # Target level for this arc: advance one tier
    tier_end = {1: 4, 2: 10, 3: 16, 4: 20}
    end_level = next(
        (v for k, v in sorted(tier_end.items()) if current_level <= k * 4 and v > current_level),
        min(current_level + 5, 20),
    )
    arc_level_range = f"{current_level}-{end_level}"
    sc = _level_scaling(arc_level_range)

    module_block = ""
    if active_modules:
        lines = ["\n## Active FoundryVTT Modules\n"]
        for mod_id, mod_info in sorted(active_modules.items()):
            lines.append(f"- **{mod_id}** ({mod_info.get('version', '?')}): {mod_info.get('title', mod_id)}")
        module_block = "\n".join(lines)

    return f"""You are extending an existing TTRPG campaign for FoundryVTT with a new story arc.

## Existing Campaign Context

**Campaign:** {camp.get('name', 'Unknown')}
**Setting:** {camp.get('description', '')}
**Theme:** {camp.get('theme', '')}
**Original level range:** {camp.get('level_range', '1-20')}

**Existing scenes (DO NOT recreate these):**
{chr(10).join(f'- {s}' for s in existing_scenes) or '(none yet)'}

**Existing NPCs (you may reference or develop these):**
{chr(10).join(f'- {n}' for n in existing_npcs) or '(none yet)'}

**Existing quests/story arcs:**
{chr(10).join(f'- {q}' for q in existing_quests + existing_arcs) or '(none yet)'}

{_lore_block(lore_context)}
## Your Task — Arc {arc_number}: Levels {arc_level_range}

Generate the next arc of this campaign covering levels {arc_level_range}. This arc should:
- Follow naturally from what came before (reference existing NPCs, locations, and plot threads)
- Escalate the stakes — threats, CR, and consequences should feel bigger than Arc {arc_number - 1}
- Introduce {sc['npcs']} new NPCs (can include evolved versions of existing ones)
- Add {sc['scenes']} new Scenes with full `scene_setup` blocks (walls, lights, sounds)
- Include {sc['encounters']} combat encounters (CR-scaled to levels {arc_level_range})
- Add {sc['quests']} new quest objectives that advance or resolve prior threads
- Introduce 1 new location that fits the escalating narrative
- Provide a clear arc climax and a hook for Arc {arc_number + 1} (if levels don't reach 20)

Keep all text fields SHORT (1-2 sentences max). Output ONLY the new content — do not repeat existing scenes or NPCs.
{module_block}

## Output Format

Return a JSON object with the SAME schema as a full campaign but containing ONLY the new arc's content:

```json
{{
  "campaign": {{
    "name": "{camp.get('name', '')}",
    "arc_number": {arc_number},
    "arc_level_range": "{arc_level_range}",
    "arc_title": "<title for this arc>",
    "arc_summary": "<1-2 sentence summary of this arc's story>"
  }},
  "npcs": [ /* new NPCs only */ ],
  "locations": [ /* new locations only */ ],
  "scenes": [ /* new scenes only, each with scene_setup */ ],
  "journal_entries": [ /* new journals */ ],
  "quest_logs": [ /* new or updated quests */ ],
  "loot_tables": [ /* new loot */ ],
  "story_arcs": [ /* this arc's story beats */ ],
  "encounters": [ /* new encounters, linked to new scene names */ ]
}}
```

{CAMPAIGN_GENERATOR_PROMPT}
"""


def _repair_common_json_slips(text: str) -> str:
    """Deterministically fix well-defined JSON slips small quantized models make.

    Conservative by design — every transform is unambiguous and leaves already-
    valid JSON unchanged. Handles:

    1. Misquoted key/value: `"hp: 136",` or `"hp: 136"` where the model wrapped
       the whole `key: value` pair in one string instead of `"hp": 136`. Only
       fires when the quoted content is `<bareword>: <json-scalar>` — i.e. an
       unquoted key followed by a number / bool / null. Never touches legitimate
       string values that merely contain a colon (those have a real key before
       them, so the line already starts with `"key": "..."`).
    2. Trailing commas before } or ].
    """
    # 1. Misquoted "key: scalar" pairs. Match a line whose value token is a
    #    single quoted string of the form "IDENT: SCALAR" (scalar = number/bool/
    #    null, optionally with surrounding spaces). Rewrite to "IDENT": SCALAR.
    #    Requires the quote to be the VALUE (preceded by ': ' or line start after
    #    indentation) so we don't corrupt keys.
    misquoted = re.compile(
        r'"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*'          # "ident:
        r'(-?\d+(?:\.\d+)?|true|false|null)\s*"'        #  scalar"
    )

    def _fix(m: "re.Match") -> str:
        return f'"{m.group(1)}": {m.group(2)}'

    repaired = misquoted.sub(_fix, text)

    # 1b. Fraction values: `"cr": 1/4` — D&D writes CR as fractions, and the
    #     model emits them literally. Convert a bare N/M appearing as a JSON
    #     value into its decimal. Only fires after ': ' and before , } ] or EOL,
    #     so it never touches fractions inside string literals.
    def _frac(m: "re.Match") -> str:
        num, den = int(m.group("num")), int(m.group("den"))
        val = num / den if den else 0
        # compact repr: 0.25 not 0.25000001; ints stay ints
        return f'{m.group("pre")}{val:g}'

    repaired = re.sub(
        r'(?P<pre>:\s*)(?P<num>\d+)\s*/\s*(?P<den>\d+)(?=\s*[,}\]\n])',
        _frac,
        repaired,
    )

    # 2. Trailing commas: , followed by whitespace then } or ]
    repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)

    return repaired


def parse_campaign_response(raw_text: str) -> Dict[str, Any]:
    """Extract and validate JSON campaign data from LLM response.

    Handles:
    - Raw JSON output
    - ```json...``` code blocks
    - Chain-of-thought preambles
    - Malformed trailing text
    """
    result = raw_text.strip()

    # Strip <think>...</think> blocks (Qwen3 reasoning tokens that leak into output)
    result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()

    # Strip code blocks
    json_match = re.search(r'```json\s*\n(.*?)```', result, re.DOTALL)
    if json_match:
        result = json_match.group(1)
    else:
        json_match = re.search(r'```\s*\n(.*?)```', result, re.DOTALL)
        if json_match:
            result = json_match.group(1)

    # Conservative, deterministic repair of well-defined small-model slips
    # (misquoted keys like "hp: 136", trailing commas). Applied BEFORE parsing.
    # These transforms are unambiguous — they never change valid JSON.
    if not _is_valid_json(result):
        repaired = _repair_common_json_slips(result)
        if _is_valid_json(repaired):
            logger.warning("[Generator] Repaired malformed JSON via _repair_common_json_slips")
        result = repaired

    # Try balanced brace extraction
    if not _is_valid_json(result):
        brace_result = _extract_balanced_json(result)
        if brace_result:
            result = brace_result

    # Parse. strict=False tolerates raw control characters (unescaped newlines,
    # tabs, etc.) inside string values — a common small-model slip that json
    # rejects by default ("Invalid control character at ...").
    try:
        data = json.loads(result, strict=False)
        return _normalize_campaign_sections(data)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse campaign JSON: {e}", exc_info=True)
        recovery = _try_recovery_json(result)
        if recovery:
            return _normalize_campaign_sections(recovery)
        raise


# Section lists the schema defines as SIBLINGS of the "campaign" metadata
# block. Smaller local models often nest them all inside "campaign" instead;
# every consumer (validate_campaign, vault sync, deploy) reads them from the
# top level, so a nested response used to validate as "0 NPCs, 0 scenes" and
# deploy nothing.
_SECTION_KEYS = (
    "scenes", "npcs", "locations", "journal_entries", "quest_logs", "quests",
    "loot_tables", "encounters", "playlists", "calendar_events", "story_arcs",
    "factions", "artifacts", "prologue",
)


def _normalize_campaign_sections(data: Dict[str, Any]) -> Dict[str, Any]:
    """Hoist section lists nested inside data["campaign"] to the top level.

    Copies (rather than moves) so anything reading them from inside the
    campaign block keeps working; only fills keys missing at the top level.
    """
    camp = data.get("campaign") if isinstance(data, dict) else None
    if not isinstance(camp, dict):
        return data
    hoisted = []
    for key in _SECTION_KEYS:
        if key not in data and isinstance(camp.get(key), list):
            data[key] = camp[key]
            hoisted.append(key)
    if hoisted:
        logger.warning(
            f"[Generator] LLM nested section lists inside 'campaign' — "
            f"hoisted to top level: {hoisted}"
        )
    return data


def _is_valid_json(text: str) -> bool:
    try:
        json.loads(text, strict=False)
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
    """Try to extract the top-level campaign JSON object from malformed output.

    Only returns a result if it contains the required 'campaign' key —
    never returns a sub-object like an NPC or location entry.
    """
    # Find the outermost { that precedes a "campaign" key
    campaign_key_pos = text.find('"campaign"')
    if campaign_key_pos == -1:
        return None

    # Walk backwards to find the enclosing opening brace
    open_pos = text.rfind('{', 0, campaign_key_pos)
    if open_pos == -1:
        return None

    chunk = _extract_balanced_json(text[open_pos:])
    if chunk:
        try:
            data = json.loads(chunk, strict=False)
            if isinstance(data, dict) and "campaign" in data:
                return data
        except json.JSONDecodeError:
            pass

    return None


def _generate_default_scene_setup(scene_type: str = "dungeon") -> Dict[str, Any]:
    """Generate a default scene_setup block based on scene type.

    Ensures all scenes have proper grid configuration for map generation.
    Uses standard grid sizes (16×12, 20×15, 24×18) at 64px per square.
    """
    # Map scene types to recommended grid sizes
    size_map = {
        "tavern": (16, 12),           # Small indoor
        "building": (16, 12),         # Small indoor
        "cottage": (16, 12),          # Small indoor
        "shop": (16, 12),             # Small indoor
        "chamber": (16, 12),          # Small indoor
        "dungeon": (20, 15),          # Medium
        "crypt": (20, 15),            # Medium
        "cave": (20, 15),             # Medium
        "settlement": (20, 15),       # Medium
        "town": (20, 15),             # Medium
        "tower": (16, 20),            # Tall indoor
        "temple": (20, 15),           # Medium
        "castle": (24, 18),           # Large
        "fortress": (24, 18),         # Large
        "battlefield": (24, 18),      # Large
        "wilderness": (24, 18),       # Large
        "outdoors": (24, 18),         # Large
        "village": (24, 18),          # Large
        "city": (24, 18),             # Large
    }

    gw, gh = size_map.get(scene_type.lower(), (20, 15))  # Default to medium

    return {
        "grid_width": gw,
        "grid_height": gh,
        "grid_size_px": 64,  # Fixed constant
        "fog_exploration": False,
        "token_vision": False,
        "global_illumination": True,
        "darkness": 0.0,
        "walls": [],
        "doors": [],
        "lights": [],
        "sounds": []
    }


def world_location_coverage_gaps(data: Dict[str, Any]) -> List[str]:
    """Return names of world-only locations that aren't woven into any content.

    A world-only location (act is null AND no scenes) earns its place when it is
    pulled into the campaign by at least one of: its own `rumors` hook, a quest
    `location`, a faction `goals` mention, an artifact `current_locations` entry,
    or another location's `connections`/`rumors`. Locations referenced nowhere
    are pure map decoration — this surfaces them so generation can be nudged.

    Unnamed stub entries are ignored (nothing to reference).
    """
    locations = data.get("locations", []) or []

    # Build one lowercased haystack of every place a location name could be cited.
    refs: List[str] = []
    for q in (data.get("quest_logs") or data.get("quests") or []):
        if isinstance(q, dict) and q.get("location"):
            refs.append(str(q["location"]))
    for f in (data.get("factions") or []):
        if isinstance(f, dict):
            refs.extend(str(g) for g in (f.get("goals") or []))
    for a in (data.get("artifacts") or []):
        if isinstance(a, dict):
            refs.extend(str(c) for c in (a.get("current_locations") or []))
    for loc in locations:
        if isinstance(loc, dict):
            refs.extend(str(c) for c in (loc.get("connections") or []))
            refs.extend(str(r) for r in (loc.get("rumors") or []))
    haystack = "\n".join(refs).lower()

    gaps: List[str] = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        name = (loc.get("name") or "").strip()
        world_only = loc.get("act") is None and not loc.get("scenes")
        if not name or not world_only:
            continue
        # Its own rumors count as earning a place, even if nothing else cites it.
        if loc.get("rumors"):
            continue
        if name.lower() in haystack:
            continue
        gaps.append(name)
    return gaps


def validate_campaign(data: Dict[str, Any], level_range: str = "1-5") -> List[str]:
    """Validate campaign structure. Returns list of warnings and auto-fixes missing scene_setup.

    Minimums are derived from the same level-scaled targets given to the LLM
    (see _level_scaling), so a short 1-5 campaign that hits its own 3-5 scene
    target doesn't get flagged against a long-campaign's 5-8/4-6 numbers.
    """
    warnings = []
    sc = _level_scaling(level_range)

    def _low_end(range_str: str) -> int:
        return int(range_str.split("-")[0])

    min_npcs = _low_end(sc["npcs"])
    min_locations = _low_end(sc["locations"])
    min_scenes = _low_end(sc["scenes"])

    campaign = data.get("campaign", {})
    if not campaign.get("name"):
        warnings.append("Campaign missing 'name' field")
    if not campaign.get("description"):
        warnings.append("Campaign missing 'description' field")

    npcs = data.get("npcs", [])
    if len(npcs) < min_npcs:
        warnings.append(f"Only {len(npcs)} NPCs defined (recommended: {sc['npcs']})")

    locations = data.get("locations", [])
    if len(locations) < min_locations:
        warnings.append(f"Only {len(locations)} locations defined (recommended: {sc['locations']})")

    # Soft coverage check: world-only locations should be woven into content
    # (rumor/quest/faction/artifact), not left as pure map decoration.
    gaps = world_location_coverage_gaps(data)
    if gaps:
        preview = ", ".join(gaps[:5]) + ("…" if len(gaps) > 5 else "")
        warnings.append(
            f"{len(gaps)} world-only site(s) not woven into content "
            f"(add a rumor, side quest, faction goal, or artifact link): {preview}"
        )

    # ── Scene validation with scene_setup enforcement ──
    scenes = data.get("scenes", [])
    if len(scenes) < min_scenes:
        warnings.append(f"Only {len(scenes)} scenes defined (recommended: {sc['scenes']})")

    # Check ALL scenes have scene_setup; auto-generate if missing
    missing_setup = []
    for i, scene in enumerate(scenes):
        scene_name = scene.get("name", f"Scene {i+1}")
        if not scene.get("scene_setup"):
            missing_setup.append(scene_name)
            # Auto-generate default scene_setup
            scene_type = scene.get("type", "dungeon")
            scene["scene_setup"] = _generate_default_scene_setup(scene_type)
            logger.warning(f"[Generator] Auto-generated scene_setup for '{scene_name}' (type: {scene_type})")
        else:
            # Validate grid dimensions are in standard set
            setup = scene["scene_setup"]
            gw = setup.get("grid_width")
            gh = setup.get("grid_height")
            gp = setup.get("grid_size_px", 64)

            # Check grid_size_px is always 64
            if gp != 64:
                setup["grid_size_px"] = 64
                warnings.append(f"Scene '{scene_name}': corrected grid_size_px to 64")

            # Verify standard grid dimensions
            standard_sizes = [(16,12), (20,15), (24,18), (16,20)]
            if (gw, gh) not in standard_sizes:
                warnings.append(
                    f"Scene '{scene_name}': grid {gw}×{gh} is non-standard. "
                    f"Recommended: 16×12 (1024×768), 20×15 (1280×960), 24×18 (1536×1152), or 16×20 (1024×1280)"
                )

    if missing_setup:
        logger.info(f"[Generator] Auto-generated scene_setup for {len(missing_setup)} scenes: {', '.join(missing_setup)}")

    quests = data.get("quest_logs", data.get("quests", []))
    min_quests = _low_end(sc["quests"])
    if len(quests) < min_quests:
        warnings.append(f"Only {len(quests)} quests defined (recommended: {sc['quests']})")

    # loot_tables / factions / artifacts are enforced against the same
    # level-scaled minimums the refill loop uses (see _level_scaling +
    # campaign_count_shortfall), so validation warnings never contradict what
    # the generator already backfilled to target.
    loot_tables = data.get("loot_tables", [])
    if len(loot_tables) < _low_end(sc["loot_tables"]):
        warnings.append(f"Only {len(loot_tables)} loot tables defined (recommended: {sc['loot_tables']})")

    factions = data.get("factions", [])
    if len(factions) < _low_end(sc["factions"]):
        warnings.append(f"Only {len(factions)} factions defined (recommended: {sc['factions']})")

    artifacts = data.get("artifacts", [])
    if len(artifacts) < _low_end(sc["artifacts"]):
        warnings.append(f"Only {len(artifacts)} artifacts defined (recommended: {sc['artifacts']})")

    # ── Prologue validation (additive — only when key exists) ──
    prologue = data.get("prologue")
    if prologue is not None:
        if not isinstance(prologue, dict):
            warnings.append("Prologue must be an object when present")
        else:
            vessel = prologue.get("vessel")
            panels = prologue.get("panels", [])
            if not vessel:
                warnings.append("Prologue missing required 'vessel' field")
            if not isinstance(panels, list) or len(panels) < 4:
                warnings.append(f"Prologue requires at least 4 panels (got {len(panels) if isinstance(panels, list) else 0})")
            else:
                # Validate panel structure
                for i, panel in enumerate(panels):
                    if not isinstance(panel, dict):
                        warnings.append(f"Prologue panel {i+1} must be an object")
                        continue
                    if not panel.get("title"):
                        warnings.append(f"Prologue panel {i+1} missing 'title'")
                    if not panel.get("body"):
                        warnings.append(f"Prologue panel {i+1} missing 'body'")
                    if not panel.get("image_prompt"):
                        warnings.append(f"Prologue panel {i+1} missing 'image_prompt'")
                    if not panel.get("era"):
                        warnings.append(f"Prologue panel {i+1} missing 'era'")

    return warnings


# Arrays the refill loop can top up, mapped to the _level_scaling key that
# supplies their target range. Order matters: scenes first (encounters link to
# them by name), then the rest.
_REFILLABLE = (
    ("scenes", "scenes"),
    ("npcs", "npcs"),
    ("locations", "locations"),
    ("encounters", "encounters"),
    ("loot_tables", "loot_tables"),
    ("factions", "factions"),
    ("artifacts", "artifacts"),
)


def campaign_count_shortfall(data: Dict[str, Any], level_range: str = "1-5") -> Dict[str, Dict[str, int]]:
    """Return arrays that fell short of their minimum target.

    Returns {array_key: {"got": N, "target_min": M, "target_range": "5-8"}} for
    every refillable array whose length is below the low end of its scaled
    target. Empty dict means the campaign meets all minimums. quest_logs is
    read from either "quest_logs" or "quests" (the schema allows both).
    """
    sc = _level_scaling(level_range)

    def low(rng: str) -> int:
        return int(rng.split("-")[0])

    checks = list(_REFILLABLE) + [("quest_logs", "quests")]
    shortfall: Dict[str, Dict[str, int]] = {}
    for key, sc_key in checks:
        if key == "quest_logs":
            got = len(data.get("quest_logs", data.get("quests", [])))
        else:
            got = len(data.get(key, []))
        target_min = low(sc[sc_key])
        if got < target_min:
            shortfall[key] = {"got": got, "target_min": target_min, "target_range": sc[sc_key]}
    return shortfall


def generate_refill_prompt(
    data: Dict[str, Any],
    shortfall: Dict[str, Dict[str, int]],
    level_range: str = "1-5",
) -> str:
    """Build a targeted top-up prompt to backfill short arrays.

    Gives the model a slim summary of the EXISTING campaign (name, theme, and
    the names already used per array so it doesn't duplicate) and asks only for
    the missing items, as a JSON object keyed by the short array names.
    """
    camp = data.get("campaign", {})
    existing = {
        "scenes": [s.get("name") for s in data.get("scenes", [])],
        "npcs": [n.get("name") for n in data.get("npcs", [])],
        "locations": [l.get("name") for l in data.get("locations", [])],
        "quest_logs": [q.get("title") for q in data.get("quest_logs", data.get("quests", []))],
        "encounters": [e.get("name") for e in data.get("encounters", [])],
        "loot_tables": [t.get("name") for t in data.get("loot_tables", [])],
        "factions": [f.get("name") for f in data.get("factions", [])],
        "artifacts": [a.get("name") for a in data.get("artifacts", [])],
    }
    scene_names = [s.get("name") for s in data.get("scenes", []) if s.get("name")]

    need_lines = []
    for key, info in shortfall.items():
        deficit = info["target_min"] - info["got"]
        already = ", ".join(n for n in existing.get(key, []) if n) or "(none)"
        need_lines.append(
            f"- {key}: you have {info['got']}, need at least {info['target_min']} "
            f"(target {info['target_range']}). Generate {deficit} MORE. "
            f"Already used (do not duplicate): {already}"
        )

    scene_hint = ""
    if "encounters" in shortfall and scene_names:
        scene_hint = (
            "\nEach new encounter MUST set \"linked_scene\" to one of these exact "
            f"scene names: {', '.join(scene_names)}."
        )

    return f"""You are extending an in-progress campaign named "{camp.get('name', 'Untitled')}".
Theme: {camp.get('theme', '')}. Levels: {level_range}.

The campaign is SHORT on some content. Generate ONLY the additional items below —
do not repeat anything already present. Each item must match the same JSON shape
used for that array in a full campaign (scenes need a full scene_setup block).

{chr(10).join(need_lines)}{scene_hint}

Return ONE JSON object whose keys are ONLY the array names above, each mapping to a
JSON array of the NEW items. Example: {{"npcs": [ ... ], "scenes": [ ... ]}}.
Output must be a JSON object from the very first character. No prose, no code fences."""


def _norm_scene(s: Optional[str]) -> str:
    """Normalize a scene name for tolerant matching (drop leading 'The', case, punctuation)."""
    s = str(s or "").lower().strip()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def reconcile_encounter_scenes(data: Dict[str, Any]) -> List[str]:
    """Ensure every encounter's ``linked_scene`` points at a real generated scene.

    Small models often invent an encounter linked to a scene name that was never
    generated (e.g. encounter 'The Vault Trap' -> 'The Brass Vault' when no such
    scene exists). At deploy time that encounter places no tokens and logs a
    warning. Fix it in the DATA before it reaches the vault/deploy:

      1. exact match           -> keep
      2. normalized match      -> rewrite to the real name (drops 'The', case, punct)
      3. fuzzy match (>=0.6)    -> rewrite to the closest real name
      4. same-act fallback     -> relink to a scene in the encounter's act
      5. first-scene fallback  -> relink to the first scene (last resort)

    Mutates ``data`` in place and returns a list of human-readable change notes.
    """
    from difflib import SequenceMatcher

    scenes = data.get("scenes", []) or []
    scene_names = [s.get("name") for s in scenes if isinstance(s, dict) and s.get("name")]
    if not scene_names:
        return []  # nothing to link to; leave encounters untouched

    norm_to_real = {_norm_scene(n): n for n in scene_names}
    # scenes grouped by act for the same-act fallback
    act_to_scene: Dict[Any, str] = {}
    for s in scenes:
        if isinstance(s, dict) and s.get("name"):
            act_to_scene.setdefault(s.get("act"), s["name"])

    changes: List[str] = []
    encounters = data.get("encounters", []) or []
    for enc in encounters:
        if not isinstance(enc, dict):
            continue
        enc_name = enc.get("name", "Unnamed Encounter")
        linked = enc.get("linked_scene", "")

        # 1. exact match — already valid
        if linked and linked in scene_names:
            continue

        resolved = None
        note = ""

        # 2. normalized match
        if linked and _norm_scene(linked) in norm_to_real:
            resolved = norm_to_real[_norm_scene(linked)]
            note = "normalized"

        # 3. fuzzy match
        if resolved is None and linked:
            best, best_score = None, 0.0
            nlinked = _norm_scene(linked)
            for real in scene_names:
                score = SequenceMatcher(None, nlinked, _norm_scene(real)).ratio()
                if score > best_score:
                    best, best_score = real, score
            if best is not None and best_score >= 0.6:
                resolved = best
                note = f"fuzzy {best_score:.2f}"

        # 4. same-act fallback
        if resolved is None:
            act_scene = act_to_scene.get(enc.get("act"))
            if act_scene:
                resolved = act_scene
                note = f"act {enc.get('act')} fallback"

        # 5. first-scene fallback (guarantees a valid link)
        if resolved is None:
            resolved = scene_names[0]
            note = "first-scene fallback"

        if resolved != linked:
            enc["linked_scene"] = resolved
            changes.append(
                f"'{enc_name}': linked_scene '{linked or '(none)'}' -> '{resolved}' ({note})"
            )

    return changes


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
            "## Factions", "",
            f"### [[{campaign.get('name', 'Campaign')}/Factions]]", "",
        ])
        for f in factions:
            lines.extend([
                f"- **{f['name']}** ({f.get('alignment', '???')}) — {f.get('description', '')}",
                f"  - Goals: {', '.join(f.get('goals', []))}",
                f"  - Strength: {f.get('strength', 'unknown')}", "",
            ])

    # NPCs
    npcs = data.get("npcs", [])
    if npcs:
        lines.extend([
            "## NPCs", "",
            f"### [[{campaign.get('name', 'Campaign')}/NPCs]]", "",
        ])
        for npc in npcs:
            npc_name = npc.get('name', 'Unnamed NPC')
            lines.extend([
                f"- **[[{campaign.get('name', 'Campaign')}/NPCs/{npc_name}]]** — {npc.get('role', 'unknown')}"
                + f" ({npc.get('alignment', '??')})"
                + f" — {npc.get('description', '')[:100]}"
                + ("..." if len(npc.get('description', '')) > 100 else ""),
            ])
        lines.append("")

    # Locations
    locations = data.get("locations", [])
    if locations:
        lines.extend([
            "## Locations", "",
            f"### [[{campaign.get('name', 'Campaign')}/Locations]]", "",
        ])
        for loc in locations:
            loc_name = loc.get('name', 'Unnamed Location')
            map_note = f"[[{campaign.get('name', 'Campaign')}/Maps/{loc_name}]]" if loc.get("map_style") else ""
            lines.extend([
                f"- **[[{campaign.get('name', 'Campaign')}/Locations/{loc_name}]]** — {loc.get('type', 'unknown')}"
                + f" (Act {loc.get('act', '?')})"
                + f" — {loc.get('description', '')[:80]}"
                + ("..." if len(loc.get('description', '')) > 80 else ""),
                f"  🗺️ {map_note}" if map_note else "",
            ])
        lines.append("")

    # Quests
    quests = data.get("quest_logs", data.get("quests", []))
    if quests:
        lines.extend([
            "## Quests", "",
            f"### [[{campaign.get('name', 'Campaign')}/Quests]]", "",
        ])
        for q in quests:
            quest_title = q.get('title', 'Untitled Quest')
            lines.extend([
                f"- **[[{campaign.get('name', 'Campaign')}/Quests/{quest_title}]]** — Act {q.get('act', '?')}"
                + f" [{q.get('type', 'side')}]: {q.get('description', '')[:100]}..."
            ])
        lines.append("")

    # Story Arcs
    story_arcs = data.get("story_arcs", [])
    if story_arcs:
        lines.extend([
            "## Story Arcs", "",
        ])
        for arc in story_arcs:
            lines.extend([
                f"### Act {arc.get('act', '?')}: {arc.get('title', 'Unnamed Act')}", "",
                arc.get("description", ""), "",
                "**Milestones:**", "",
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
            "## Artifacts & McGuffins", "",
        ])
        for art in artifacts:
            lines.extend([
                f"### {art.get('name', 'Unnamed Artifact')} [{art.get('type', 'common')}]", "",
                art.get("description", ""), "",
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
        f"# [[{campaign_name}]]/{npc.get('name', 'Unknown NPC')}", "",
        f"tags: [npc, {npc.get('role', 'unknown')}]", "",
        f"Role: {npc.get('role', 'unknown')}",
        f"Faction: {npc.get('faction', 'None')}",
        f"Alignment: {npc.get('alignment', '??')}", "",
        f"## Description", "",
        npc.get("description", ""), "",
        f"## Personality", "",
        " ".join(f"- {t}" for t in npc.get("personality", ["mysterious"])), "",
        f"## Motivations", "",
        " ".join(f"- {m}" for m in npc.get("motivations", ["unknown"])), "",
        f"## Relationships", "",
        " ".join(f"- {r}" for r in npc.get("relationships", ["neutral to all"])), "",
        f"## Stat Block", "",
        npc.get("stat_block", "TBD"), "",
        f"## First Appearance", "",
        npc.get("first_appearance", "TBD"), "",
    ]
    return "\n".join(lines)


def build_location_markdown(campaign_name: str, loc: Dict) -> str:
    """Build individual location note content for Obsidian."""
    lines = [
        f"# [[{campaign_name}]]/{loc.get('name', 'Unknown Location')}", "",
        f"tags: [location, {loc.get('type', 'unknown')}]", "",
        f"Type: {loc.get('type', 'unknown')}",
        f"Act: {loc.get('act', '?')}", "",
        f"## Description", "",
        loc.get("description", ""), "",
        f"## Key Features", "",
        " ".join(f"- {f}" for f in loc.get("key_features", [])), "",
        f"## Connections", "",
        " ".join(f"- {c}" for c in loc.get("connections", [])), "",
    ]

    rumors = loc.get("rumors", [])
    if rumors:
        lines.extend([
            "## Rumors & Hooks", "",
            "\n".join(f"- {r}" for r in rumors), "",
        ])

    if loc.get("map_style"):
        lines.extend([
            f"## Map", "",
            f"Map style: {loc['map_style']}",
            f"Map file: `maps/{loc.get('name', '').lower().replace(' ', '_')}_map.png`", "",
        ])

    return "\n".join(lines)


def build_quest_markdown(campaign_name: str, quest: Dict) -> str:
    """Build individual quest note content for Obsidian."""
    lines = [
        f"# [[{campaign_name}]]/Quests/{quest.get('title', 'Unknown Quest')}", "",
        f"tags: [quest, {quest.get('type', 'side')}]", "",
        f"Type: {quest.get('type', 'side')}",
        f"Act: {quest.get('act', '?')}",
        f"Status: {quest.get('status', 'not-started')}", "",
        f"## Description", "",
        quest.get("description", ""), "",
        f"## Objectives", "",
    ]
    for i, obj in enumerate(quest.get("objectives", []), 1):
        lines.append(
            f"{i}. {obj.get('desc', '')}"
            + (f" — Check: {obj.get('check', '')}" if obj.get('check') else "")
        )
    lines.append("")

    if quest.get("rewards"):
        lines.extend([
            "## Rewards", "",
            " ".join(f"- {r}" for r in quest.get("rewards", [])), "",
        ])

    if quest.get("consequences"):
        lines.extend([
            "## Consequences", "",
            f"- **Success:** {quest['consequences'].get('success', '')}",
            f"- **Failure:** {quest['consequences'].get('failure', '')}", "",
        ])

    return "\n".join(lines)
