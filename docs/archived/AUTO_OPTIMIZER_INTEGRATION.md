# AutoOptimizer Integration Guide

The AutoOptimizer automatically enriches newly created scenes, encounters, and quests with module-based enhancements. This ensures every dynamically generated story element is optimized for the player's Foundry module ecosystem.

## API Endpoints

### Auto-Optimize Scene
```bash
POST /api/campaign/auto-optimize-scene
Content-Type: application/json

{
  "scene": {
    "name": "The Hidden Chamber",
    "description": "A mysterious underground chamber...",
    "scene_setup": { ... }
  },
  "campaign_name": "The Ashen Crown"  # optional, uses current campaign if omitted
}
```

**Response:**
```json
{
  "status": "optimized",
  "scene": "The Hidden Chamber",
  "enhancements": {
    "scene_name": "The Hidden Chamber",
    "modules": { ... },
    "synergies": [
      {
        "scene": "The Hidden Chamber",
        "synergies": [
          {
            "module": "Notification Tooltips",
            "enhancement": "Display atmospheric descriptions",
            "implementation": "Show environmental awareness hints"
          }
        ]
      }
    ],
    "recommendations": [ ... ]
  }
}
```

### Auto-Optimize Encounter
```bash
POST /api/campaign/auto-optimize-encounter
Content-Type: application/json

{
  "encounter": {
    "name": "Ambush in the Crypt",
    "description": "Undead guardians attack!",
    "tokens_placed": 4
  },
  "campaign_name": "The Ashen Crown"  # optional
}
```

### Auto-Optimize Quest
```bash
POST /api/campaign/auto-optimize-quest
Content-Type: application/json

{
  "quest": {
    "title": "Recover the Lost Amulet",
    "description": "The amulet was hidden by...",
    "stages": [ ... ]
  },
  "campaign_name": "The Ashen Crown"  # optional
}
```

## Integration in Python Code

### When Creating Scenes Dynamically

```python
from campaign.auto_optimizer import AutoOptimizer

# After creating a new scene
new_scene = {
    "name": "The Dragon's Lair",
    "description": "A massive cavern filled with gold...",
    "scene_setup": { "walls": [], "lights": [] }
}

# Auto-optimize immediately
optimizer = AutoOptimizer(llm_manager=llm_manager, foundry_client=foundry_client)
enhancements = await optimizer.optimize_new_scene(new_scene, campaign_data)

# Use enhancements to configure scene in Foundry
if "synergies" in enhancements:
    for synergy in enhancements["synergies"]:
        # Apply synergy recommendations to scene setup
        pass
```

### When Creating Encounters Dynamically

```python
# After building a combat encounter
new_encounter = {
    "name": "Boss Battle",
    "description": "Face the final enemy!",
    "tokens_placed": 5
}

# Auto-optimize
enhancements = await optimizer.optimize_new_encounter(new_encounter, campaign_data)

# Extract dramatic moments
dramatic_beats = enhancements.get("recommendations", [])
for rec in dramatic_beats:
    # Use recommendations in combat narration
    logger.info(f"Drama tip: {rec}")
```

### When Creating Quests During Play

```python
# After story generation creates a new quest
new_quest = {
    "title": "The Prophecy Unfolds",
    "description": "An ancient prophecy...",
    "stages": [
        {"description": "Stage 1: Investigation"},
        {"description": "Stage 2: Confrontation"},
    ]
}

# Auto-optimize
enhancements = await optimizer.optimize_new_quest(new_quest, campaign_data)

# Use narrative enhancements in quest descriptions
narrative_hooks = enhancements.get("narrative_enhancements", {})
```

### Batch Optimization

```python
# Optimize multiple new scenes at once
new_scenes = [
    {"name": "Scene 1", ...},
    {"name": "Scene 2", ...},
    {"name": "Scene 3", ...},
]

results = await optimizer.optimize_element_batch(
    new_scenes, 
    element_type="scene", 
    campaign_data=campaign_data
)
```

## Integration Points in Orchestrator

### In `orchestrator.enrich_scenes()`
After placing walls, add scene optimization:

```python
# After walls are placed
await foundry_client.canvas_create("walls", walls)

# Auto-optimize the scene with available modules
try:
    enhancements = await auto_optimizer.optimize_new_scene(
        scene, 
        campaign_data
    )
    # Store enhancements for reference
    scene["_module_enhancements"] = enhancements
    logger.info(f"Scene enriched with {len(enhancements.get('synergies', []))} module synergies")
except Exception as e:
    logger.warning(f"Scene optimization skipped: {e}")
```

### In Story Generation
When creating dynamic quests/encounters:

```python
# After LLM generates a new quest
generated_quest = await llm_manager.generate_quest(...)

# Auto-optimize it
enhancements = await auto_optimizer.optimize_new_quest(generated_quest, campaign_data)

# Feed enhancements back into quest data
generated_quest["_enhancements"] = enhancements

# Deploy to Foundry
await foundry_client.create_quest(generated_quest)
```

### In Combat System
When dynamic encounters are generated:

```python
# After combat encounter is assembled
encounter = await combat_generator.build_encounter(difficulty="hard")

# Auto-optimize for maximum drama
enhancements = await auto_optimizer.optimize_new_encounter(encounter, campaign_data)

# Use dramatic recommendations in combat narration
for rec in enhancements.get("recommendations", []):
    gm_narrator.add_dramatic_beat(rec["action"])
```

## What Enhancements Include

Each optimization returns:

1. **Module Synergies**
   - Which modules apply to this element
   - Specific enhancement suggestions
   - Implementation guidance

2. **Recommendations**
   - Priority-ordered suggestions
   - Category-based (Immersion, Drama, NPC Management, etc.)
   - Actionable by the GM

3. **Narrative Hooks**
   - Story-enhancing opportunities
   - Dialogue suggestions
   - Atmosphere guidance

## Flow Diagram

```
Story/Encounter/Scene Created
    ↓
AutoOptimizer.optimize_*()
    ↓
Analyze with Campaign Context
    ↓
Query Available Modules
    ↓
LLM Maps Module Synergies
    ↓
Return Enhancements + Recommendations
    ↓
Feed Back into Foundry Setup
    ↓
Enhanced Element Deployed
```

## Performance Considerations

- **Async**: All optimization calls are async - don't block story generation
- **Background**: Run optimization in background if story flow is critical
- **Caching**: Enhancements are stored in scene/encounter/quest data for reuse
- **LLM Cost**: Each optimization uses LLM tokens - consider batching

## Fallback Behavior

If optimization fails:
- Scene/encounter/quest still deploys without enhancements
- Error is logged but doesn't block deployment
- Manual optimization available via `/api/campaign/analyze-and-optimize` endpoint

## Example: Full Scene Creation Flow

```python
async def create_scene_with_optimization(
    scene_name: str, 
    description: str, 
    campaign_data: dict,
    llm_manager,
    foundry_client,
    auto_optimizer: AutoOptimizer
):
    # 1. Create base scene
    scene = {
        "name": scene_name,
        "description": description,
        "scene_setup": {"walls": [], "lights": [], "sounds": []}
    }
    
    # 2. Generate scene details
    scene = await enrich_scene_with_details(scene, campaign_data)
    
    # 3. Create in Foundry
    scene_uuid = await foundry_client.create_scene(scene)
    scene["uuid"] = scene_uuid
    
    # 4. Place walls (existing enrichment)
    walls = await generate_walls_for_scene(scene)
    if walls:
        await foundry_client.canvas_create("walls", walls)
    
    # 5. Auto-optimize with modules
    try:
        enhancements = await auto_optimizer.optimize_new_scene(scene, campaign_data)
        scene["_enhancements"] = enhancements
        
        # 6. Apply enhancement recommendations
        if "synergies" in enhancements:
            await apply_enhancements_to_scene(scene, enhancements, foundry_client)
    
    except Exception as e:
        logger.warning(f"Module optimization skipped: {e}")
    
    # 7. Scene fully deployed
    return scene
```

This ensures every story element is automatically optimized for immersion!
