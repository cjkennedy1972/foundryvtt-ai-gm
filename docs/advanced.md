# Advanced Features Guide

## Procedural Generation

All four procedural generators now deploy their output directly to FoundryVTT when the engine is connected — no manual import step needed.

**Generate and place an encounter:**
```bash
curl "http://localhost:18080/api/procedural/encounter?party_level=5&party_size=4"
```
Creates Foundry actors for each monster type, places tokens on the active scene, and starts an encounter.

**Generate and place an NPC:**
```bash
curl "http://localhost:18080/api/procedural/npc?role=innkeeper"
```
Creates a Foundry actor with HP and biography, then places a token on the current scene.

**Generate treasure (creates a Foundry journal entry):**
```bash
curl "http://localhost:18080/api/procedural/treasure?cr=5"
```

**Generate a quest (creates a Foundry journal entry):**
```bash
curl "http://localhost:18080/api/procedural/quest?theme=rescue"
```

---

## Campaign Build Pipeline

The campaign build pipeline runs six phases sequentially. Each phase is checkpointed — if the build crashes mid-way, re-running it resumes from the last completed phase rather than starting over.

**Phases:**
1. **Scan** — Detect existing Foundry scenes, actors, modules, and capabilities
2. **Generate** — LLM produces the full campaign structure (JSON)
3. **Vault** — Save campaign data to Obsidian vault as markdown
4. **Assets** — Generate battle maps and NPC portraits (ComfyUI or oMLX)
5. **Upload** — Upload images to Foundry; attach as scene backgrounds
6. **Deploy** — Create scenes, NPCs, journals, loot tables, quest logs, playlists
7. **Enrich** — Place walls, lights, sounds; configure fog and darkness per scene

The checkpoint file lives at `campaign_assets/<campaign-name>/build_checkpoint.json` and is deleted automatically on successful completion.

---

## Combat System

### LLM Timeout & Fallback

NPC turns are bounded by `llm_combat_timeout` (default 60 seconds, configurable in `.env` as `LLM_COMBAT_TIMEOUT`). If the LLM is unresponsive, the combat loop falls back to generic NPC behavior — move toward the nearest PC and make a basic attack — so combat never freezes.

### Combat Snapshots

A state snapshot is saved automatically at the start of every combat. Retrieve it via:

```bash
curl http://localhost:18080/api/combat/snapshot
```

The snapshot includes all token positions and actor stats from before the first round. Use it as a reference if you need to manually restore state after something goes wrong.

### Difficulty Suggestions

```bash
curl "http://localhost:18080/api/combat/difficulty/suggestions?party_level=5&party_size=4"
```
Returns Easy/Medium/Hard/Deadly CR targets for the party.

### Tactical Analysis

```bash
curl -X POST http://localhost:18080/api/combat/tactical/analyze \
  -H "Content-Type: application/json" \
  -d '{"scene_name": "Goblin Lair", "combatants": ["grok", "elara", "goblin_1"]}'
```

---

## Scene Automation

When deploying a campaign, scenes are automatically enriched with:

- **NPC Placement** — NPCs with matching `first_appearance` fields are placed in the correct scene
- **Fog of War** — Vision ranges extracted from scene lighting specs
- **Hazard Visualization** — Color-coded overlay zones for traps, spikes, fire, water, obstacles
- **Ambient Sound** — Scene `atmosphere` field auto-selects matching audio
- **GM Macros** — Initiative, Perception, Short/Long Rest, round timer macros

Scene data in campaign JSON:
```json
{
  "name": "Tavern",
  "lighting": {"type": "dim", "sources": ["torch (20ft), chandelier (40ft)"]},
  "atmosphere": "bustling tavern",
  "hazards": [{"name": "weak floor", "type": "obstacle", "x": 300, "y": 400}],
  "encounter": {"has_encounter": false, "enemies": []}
}
```

---

## NPC System

### Personality

```bash
curl -X POST http://localhost:18080/api/npc/personality \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Grok the Barbarian",
    "personality": {
      "goals": "Find lost tribe",
      "fears": "Betrayal by friends",
      "flaws": "Aggressive when drunk",
      "bonds": "Loyal to party",
      "ideals": "Strength and honor"
    }
  }'
```

### Relationships

```bash
curl -X POST http://localhost:18080/api/npc/relationship \
  -H "Content-Type: application/json" \
  -d '{"npc_a": "Grok", "npc_b": "Elara", "relationship": "rivals", "history": "competed for chief role"}'
```

---

## Context Management

The AI maintains conversation history within the configured token budget (`MAX_CONTEXT_TOKENS`, default 50,000). Context reinforcement injects anchor facts every N turns to prevent drift.

**Trigger a manual reinforcement pass:**
```bash
curl -X POST http://localhost:18080/api/context/reinforce
```

**Generate a world summary:**
```bash
curl -X POST http://localhost:18080/api/context/world_summary
```

---

## ComfyUI Map Generation

The system generates D&D battle maps and NPC portraits using ComfyUI + SDXL. This is optional but enhances campaign immersion significantly.

**Requirements:**
- ComfyUI 0.24.1+
- Checkpoint: `dDBattlemapsSDXL10_upscaleV10.safetensors`

**Setup:**
```bash
# Start ComfyUI
python main.py --port 18188   # inside ComfyUI directory

# Verify
cd ai-engine/campaign/workflows && python verify_comfyui_setup.py
```

**Configuration** in `ai-engine/.env`:
```
COMFYUI_BASE_URL=http://localhost:18188
COMFYUI_CHECKPOINT=dDBattlemapsSDXL10_upscaleV10.safetensors
```

Generation times on M-series Mac: ~100s per map, ~80s per portrait. Maps are 1024×768, portraits 512×768.

See [ai-engine/campaign/workflows/SETUP_GUIDE.md](../ai-engine/campaign/workflows/SETUP_GUIDE.md) for detailed setup and troubleshooting.

---

## Obsidian Vault Structure

```
Dungeons_and_Dragons/
├── [Campaign Name]/
│   ├── campaign.json            # Generated campaign data
│   ├── Campaign State.md        # Factions, current goals, timeline
│   ├── Act I - Chapter 1.md     # Plot outline
│   ├── NPCs - Act I.md          # Personalities & relationships
│   ├── Worldbuilding.md         # Setting & lore
│   ├── Character Hooks.md       # PC backstories & motivations
│   ├── Session 1 — Opening.md  # Pacing, encounters, beats
│   └── DM_Reference.md          # House rules & GM notes
└── Rules/
    └── DnD_SRD_5e_Full.txt      # Optional: full SRD for rules lookups
```

**Tips for best results:**
- Include NPC goals, fears, quirks, and faction relationships
- Document plot hooks tied to specific party members
- Add scene-by-scene pacing notes (combat-heavy, roleplay, exploration)
- Use session files (Session 1.md, Session 2.md) for planned beats

---

## AI Configuration Tips

**Model selection:**
| Model | Best for |
|-------|----------|
| Claude Sonnet 4.6 | Narrative quality, roleplay depth |
| GPT-4o | Tactical decisions, rules application |
| Gemini 2.5 Pro | Fast responses, real-time chat |
| Llama 3.3 70B | Local inference on dedicated GPU |

**Temperature:**
- `0.5` — Predictable, good for rules-heavy combat
- `0.7` (default) — Balanced narrative and consistency
- `0.9` — Creative surprises, personality variation

**System prompt customization** — Edit `ai-engine/llm/system_prompts.py` to adjust tone, add house rules, or emphasize specific NPC personalities.
