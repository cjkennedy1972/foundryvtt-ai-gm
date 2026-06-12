"""
Action executors — each function executes one type of GM action in FoundryVTT.
"""

import logging
from typing import Optional, Any

from foundry.client import FoundryClient

logger = logging.getLogger(__name__)


async def execute_narrate(text: str, foundry: FoundryClient) -> dict:
    """Send narration as GM in Foundry chat."""
    result = await foundry.chat_message(text, speaker=foundry._get_speaker_name(), whisper=[])
    logger.info(f"[Narrate] {text[:80]}...")
    return {"type": "narrate", "result": result}


async def execute_speak(
    npc_name: str, text: str, whisper_to: Optional[str] = None, foundry: FoundryClient = None
) -> dict:
    """Speak as an NPC in Foundry chat."""
    whisper_list = [whisper_to] if whisper_to else []
    result = await foundry.chat_message(text, speaker=npc_name, whisper=whisper_list)
    whisper_note = f" (whisper to {whisper_to})" if whisper_to else ""
    logger.info(f"[{npc_name}{whisper_note}] {text[:80]}...")
    return {"type": "speak", "npc": npc_name, "result": result}


async def execute_roll(
    formula: str, speaker: str, flavor: Optional[str] = None, foundry: FoundryClient = None
) -> dict:
    """Roll dice in Foundry."""
    result = await foundry.roll(formula, speaker=speaker, flavor=flavor)
    logger.info(f"[Roll] {formula} by {speaker} → {result.get('result', 'unknown')}")
    return {"type": "roll", "formula": formula, "speaker": speaker, "result": result}


async def execute_move_token(
    token_id: str, x: float, y: float, foundry: FoundryClient = None
) -> dict:
    """Move a token on the grid."""
    result = await foundry.update_entity(uuid=None, data={"token": {"x": x, "y": y}},
                                          token_id=token_id)
    logger.info(f"[Move] Token {token_id} → ({x}, {y})")
    return {"type": "move_token", "token_id": token_id, "result": result}


async def execute_update_hp(
    actor_uuid: str, damage: int, foundry: FoundryClient = None
) -> dict:
    """Apply damage (positive) or healing (negative) to an actor."""
    if damage > 0:
        result = await foundry.decrease_attribute("hp.value", damage, actor_uuid)
        logger.info(f"[Damage] {actor_uuid} took {damage} damage")
    else:
        result = await foundry.increase_attribute("hp.value", abs(damage), actor_uuid)
        logger.info(f"[Heal] {actor_uuid} healed {-damage} HP")
    return {"type": "update_hp", "actor_uuid": actor_uuid, "damage": damage, "result": result}


async def execute_play_sound(
    sound_name: str, foundry: FoundryClient = None
) -> dict:
    """Play a sound effect in Foundry."""
    result = await foundry.play_sound(sound_name)
    logger.info(f"[Sound] {sound_name}")
    return {"type": "play_sound", "sound_name": sound_name, "result": result}


async def execute_switch_scene(
    scene_name: str, foundry: FoundryClient = None
) -> dict:
    """Change the current scene."""
    result = await foundry.set_active_scene(scene_name)
    logger.info(f"[Scene] Switched to {scene_name}")
    return {"type": "switch_scene", "scene_name": scene_name, "result": result}


async def execute_start_encounter(
    token_ids: list, foundry: FoundryClient = None
) -> dict:
    """Begin combat."""
    result = await foundry.start_encounter(tokens=token_ids)
    logger.info(f"[Combat] Started encounter with {len(token_ids)} tokens")
    return {"type": "start_encounter", "token_ids": token_ids, "result": result}


async def execute_end_encounter(foundry: FoundryClient = None) -> dict:
    """End combat."""
    result = await foundry.end_encounter()
    logger.info("[Combat] Ended encounter")
    return {"type": "end_encounter", "result": result}


async def execute_prompt_player(
    player: str, question: str, foundry: FoundryClient = None
) -> dict:
    """Ask a specific player for input — send as a chat message to their view."""
    result = await foundry.chat_message(question, speaker=player, whisper=[player])
    logger.info(f"[Prompt] {question}")
    return {"type": "prompt_player", "player": player, "result": result}


# Action handler registry
ACTION_HANDLERS = {
    "narrate": execute_narrate,
    "speak": execute_speak,
    "roll": execute_roll,
    "move_token": execute_move_token,
    "update_hp": execute_update_hp,
    "play_sound": execute_play_sound,
    "switch_scene": execute_switch_scene,
    "start_encounter": execute_start_encounter,
    "end_encounter": execute_end_encounter,
    "prompt_player": execute_prompt_player,
}
