"""Procedural content generation endpoints (Tier 5): encounters, treasure, NPCs, quests."""

import logging

from fastapi import APIRouter, Depends

from actions.executors import _require
from api.deps import AppState, get_app_state

logger = logging.getLogger("ai-gm")

router = APIRouter(prefix="/api/procedural", tags=["procedural"])


@router.get("/encounter")
async def generate_encounter(
    difficulty: str = "medium", party_level: int = 5, party_size: int = 4,
    state: AppState = Depends(get_app_state)
):
    """Generate a random encounter and deploy to Foundry.

    This endpoint generates an encounter and immediately places monster tokens
    in the active Foundry scene. Returns the encounter data plus token IDs
    and deployed status.
    """
    _require(state.action_dispatcher, "Action dispatcher not initialized")
    _require(state.foundry_client, "Foundry client not initialized")

    try:
        # Use the action dispatcher to execute properly validated generation
        result = await state.action_dispatcher.execute({
            "type": "generate_encounter",
            "party_level": party_level,
            "party_size": party_size,
        })
        return result
    except Exception as e:
        logger.error(f"[Procedural] Encounter generation failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "type": "generate_encounter",
            "success": False
        }


@router.get("/treasure")
async def generate_treasure(
    treasure_cr: float = 2.0, level: int = 5,
    state: AppState = Depends(get_app_state)
):
    """Generate random treasure."""
    from procedural.treasures import TreasureGenerator
    gen = TreasureGenerator()
    treasure = gen.generate(treasure_cr, level)
    return {
        "treasure": {
            "gold": treasure.gold,
            "gems": treasure.gems,
            "items": treasure.items,
            "magical_items": treasure.magical_items,
            "total_value": treasure.total_value,
        }
    }


@router.get("/npc")
async def generate_npc(state: AppState = Depends(get_app_state)):
    """Generate a random NPC."""
    from procedural.npcs import NPCGenerator
    gen = NPCGenerator()
    npc = gen.generate()
    return {
        "npc": {
            "name": npc.name,
            "race": npc.race,
            "class": npc.class_name,
            "level": npc.level,
            "personality_traits": npc.personality_traits,
            "ideals": npc.ideals,
            "bonds": npc.bonds,
            "flaws": npc.flaws,
            "appearance": npc.appearance,
            "background": npc.background,
        }
    }


@router.get("/party")
async def generate_party(
    size: int = 4, level: int = 5,
    state: AppState = Depends(get_app_state)
):
    """Generate a random party of NPCs."""
    from procedural.npcs import NPCGenerator
    gen = NPCGenerator()
    party = gen.generate_party(size, level)
    return {
        "party": [
            {
                "name": npc.name,
                "race": npc.race,
                "class": npc.class_name,
                "level": npc.level,
                "personality_traits": npc.personality_traits,
            }
            for npc in party
        ]
    }


@router.get("/quest")
async def generate_quest(
    level: int = 5,
    state: AppState = Depends(get_app_state)
):
    """Generate a random quest."""
    from procedural.quests import QuestGenerator
    gen = QuestGenerator()
    quest = gen.generate(level)
    return {
        "quest": {
            "title": quest.title,
            "description": quest.description,
            "quest_giver": quest.quest_giver,
            "objective": quest.objective,
            "reward": quest.reward,
            "complications": quest.complications,
            "resolution_options": quest.resolution_options,
        }
    }


@router.get("/session")
async def generate_session(
    party_level: int = 5, party_size: int = 4,
    state: AppState = Depends(get_app_state)
):
    """Generate a full session's worth of content."""
    from procedural.generator import ProceduralGenerator
    gen = ProceduralGenerator()
    content = gen.generate_session(party_level, party_size)
    return {
        "session": {
            "encounters": [
                {
                    "name": e.name,
                    "description": e.description,
                    "difficulty": e.difficulty,
                    "monsters": e.monsters,
                }
                for e in content["encounters"]
            ],
            "quests": [
                {
                    "title": q.title,
                    "objective": q.objective,
                    "reward": q.reward,
                }
                for q in content["quests"]
            ],
            "npcs": [
                {
                    "name": n.name,
                    "race": n.race,
                    "class": n.class_name,
                    "level": n.level,
                }
                for n in content["npcs"]
            ],
        }
    }


@router.post("/dungeon/multi-level")
async def generate_multi_level_dungeon(
    name: str = "The Depths",
    floors: int = 3,
    width: int = 100,
    height: int = 100,
    connect_floors: bool = True,
    state: AppState = Depends(get_app_state)
):
    """Generate a multi-level dungeon with FoundryVTT Scene Levels structure.

    Returns Foundry Scene Levels data for direct scene import, enabling
    multi-floor dungeons in a single scene (V14+).

    Args:
        name: Dungeon name
        floors: Number of levels (3-6 recommended)
        width/height: Per-level dimensions in grid units
        connect_floors: Add stairs connecting adjacent levels
    """
    from procedural.layout_gen import MultiLevelDungeonGenerator

    try:
        gen = MultiLevelDungeonGenerator()
        dungeon = gen.generate_multi_level_dungeon(
            name=name,
            floor_count=floors,
            width=width,
            height=height,
            connect_floors=connect_floors
        )

        return {
            "dungeon": {
                "name": dungeon["name"],
                "floors": len(dungeon["levels"]),
                "foundry_levels": dungeon["foundry_levels"],
                "tiles": dungeon["tiles"],
                "lights": dungeon["lights"],
                "walls": [
                    wall
                    for level in dungeon["levels"]
                    for wall in level.walls
                ],
                "stairs": [
                    stair
                    for level in dungeon["levels"]
                    for stair in level.stairs
                ],
                "import_instructions": (
                    "1. Create a new Scene in Foundry\n"
                    "2. Add the levels via Scene > Level Manager\n"
                    "3. Import tiles, walls, and lights via foundry_levels data\n"
                    "4. Use Change Level Region behavior for stair transitions"
                )
            }
        }
    except Exception as e:
        logger.error(f"[Procedural] Multi-level dungeon generation failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "type": "generate_multi_level_dungeon",
            "success": False
        }
