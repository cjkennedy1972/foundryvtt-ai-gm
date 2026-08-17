# Scene Levels Integration Audit

## Overview

**Scene Levels** (FoundryVTT V14+) is a framework for multi-level scenes (dungeons, towers, buildings). Current codebase has **partial support** but **P2c (Procedural Layouts) doesn't leverage it yet**.

## Current State

### ✅ What's Implemented

**Foundry Client** (`ai-engine/foundry/client.py`):
- `get_scene_by_name()` — Extracts `levels` array from scene data
- Token movement respects levels: mirrors current level on token placement
- Auto-detects first level if scene has multiple levels
- Handles "level must exist" requirement for token updates

**System Prompts** acknowledge multi-level scenes:
> "**Levels** - Multi-level/multi-floor scenes"

### ❌ What's Missing

**P2c Procedural Layouts** (`ai-engine/procedural/layout_gen.py`):
- Only generates single-level dungeons
- No multi-floor support
- No Scene Levels data structure export
- No cross-level asset handling (walls, lights visible from multiple floors)

**Scene Awareness** doesn't optimize for levels:
- Doesn't distinguish between levels when querying layout
- No "which level is this room on" metadata
- No inter-level navigation awareness

**Procedural API** (`ai-engine/api/routes/procedural.py`):
- Generates standalone scenes
- Doesn't create leveled scenes as option

---

## Recommendations

### Priority 1: Extend P2c to Support Multi-Level Dungeons

**File:** `ai-engine/procedural/layout_gen.py`

**Add:**
```python
@dataclass
class DungeonLevel:
    """A single floor/level in a multi-level dungeon."""
    level_id: str           # Foundry level UUID
    floor_name: str         # "Dungeon Level 1", "Ground Floor", etc.
    elevation: int          # Z-offset for vision
    rooms: List[Room]       # Rooms on this level
    walls: List[Dict]       # Foundry walls

class MultiLevelDungeonGenerator:
    """Generate multi-level dungeons with Scene Levels support."""
    
    async def generate_multi_level_dungeon(
        self,
        name: str,
        floor_count: int = 3,
        connect_floors: bool = True
    ) -> Dict[str, Any]:
        """Generate multi-level dungeon with Scene Levels structure."""
        levels = []
        
        for floor_num in range(floor_count):
            level = self._generate_level(floor_num)
            levels.append(level)
        
        # If enabled, add stairs/passages connecting floors
        if connect_floors:
            self._add_inter_level_connections(levels)
        
        return {
            "name": name,
            "levels": levels,
            "scene_level_data": self._export_foundry_levels(levels)
        }
    
    def _export_foundry_levels(self, levels: List[DungeonLevel]) -> List[Dict]:
        """Export Scene Levels data for Foundry import."""
        return [
            {
                "_id": level.level_id,
                "name": level.floor_name,
                "elevation": level.elevation,
                "shown": True
            }
            for level in levels
        ]
```

**Benefits:**
- Dungeons stay in one scene (cleaner sidebar)
- Cross-level visibility for outer walls, trees, lights
- Tokens appear on correct floor automatically
- Stairs/passages can transition players between levels via Change Level Region

### Priority 2: Scene Levels Awareness in ChatListener

**File:** `ai-engine/foundry/chat_listener.py`

**Add level context when narrating:**
```python
async def _narrate_turn(self, ...):
    # Check if current scene has multiple levels
    scene_levels = scene.get("levels", [])
    if len(scene_levels) > 1:
        # Remind system prompt about available levels
        # Include in context: which level party is on
        # Restrict descriptions to visible areas on that level
        pass
```

### Priority 3: Scene Levels Procedural API

**File:** `ai-engine/api/routes/procedural.py`

**Add endpoint:**
```python
@router.post("/generate/multi-level-dungeon")
async def generate_multi_level_dungeon(request: MultiLevelDungeonRequest):
    """Generate a multi-level dungeon with Scene Levels structure."""
    generator = MultiLevelDungeonGenerator()
    dungeon = await generator.generate_multi_level_dungeon(
        name=request.name,
        floor_count=request.floors,
        connect_floors=True
    )
    
    # Return Foundry-importable scene with levels
    return {
        "scene": dungeon,
        "foundry_import": dungeon["scene_level_data"]
    }
```

---

## When to Use Scene Levels

| Scenario | Use Scene Levels? | Why |
|----------|------------------|-----|
| Single-room tavern | ❌ No | One scene per location is fine |
| Multi-floor dungeon (3+ levels) | ✅ Yes | Keeps sidebar clean, handles cross-level visibility |
| Tower with 4+ floors | ✅ Yes | Common in fantasy campaigns |
| Underground complex with multiple depth layers | ✅ Yes | Better than separate "Dungeon 1", "Dungeon 2" scenes |
| Castle with basement + ground + 2nd floor | ✅ Yes | Stairs/portcullis visible across levels |
| Single large cavern | ❌ No | Use height-based vision instead |
| Linear dungeon corridor | ❌ No | Separate scenes are fine |

---

## Implementation Timeline

**Phase 1 (Next):** Add multi-level generation to P2c
- Extend `ProceduralLayoutGenerator` with `MultiLevelDungeonGenerator`
- Add tests for multi-level layout generation
- Export Scene Levels data structure

**Phase 2 (Optional):** Scene Levels awareness in ChatListener
- Extract level info from scenes
- Pass to system prompt for context
- Restrict narration to visible areas per level

**Phase 3 (Polish):** API & automation
- REST endpoint for multi-level dungeon generation
- Auto-detect when Scene Levels is appropriate
- Import directly into world

---

## Testing Checklist

- [ ] Multi-level dungeons generate valid rooms/walls per floor
- [ ] Exported Scene Levels data is Foundry-compatible
- [ ] Cross-level assets (outer walls) appear on multiple levels
- [ ] Stairs/passages connect levels correctly
- [ ] Tokens placed on correct level when scene has levels
- [ ] ChatListener respects level visibility when narrating
- [ ] Player tokens can transition between levels via regions

---

## Related Code

- `ai-engine/foundry/client.py:get_scene_by_name()` — Already extracts levels
- `ai-engine/foundry/client.py:place_token()` — Already handles level assignment
- `ai-engine/procedural/layout_gen.py` — Where to extend
- `ai-engine/api/routes/procedural.py` — Where to add endpoint

---

## Summary

**Current:** Codebase is Scene Levels-aware but procedural generator doesn't use it.

**Recommendation:** Extend P2c to generate multi-level dungeons when appropriate (3+ floors). This will:
- Keep Scenes sidebar cleaner
- Enable proper cross-level visibility
- Leverage FoundryVTT V14's new framework
- Improve UX for multi-story campaigns

**Effort:** ~2-3 days (low priority, enhancement only)
