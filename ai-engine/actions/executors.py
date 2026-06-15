"""
Action executors — each function executes one type of GM action in FoundryVTT.

Each executor receives *validated* arguments from the dispatcher (Pydantic
schemas have already ensured correct types, ranges, and field names).
"""

import logging
from typing import Optional, Any

from foundry.client import FoundryClient

logger = logging.getLogger(__name__)


async def execute_narrate(text: str, foundry: FoundryClient) -> dict:
    """Send narration as GM in Foundry chat."""
    result = await foundry.chat_message(
        text, speaker=foundry._get_speaker_name(), whisper=[]
    )
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
    formula: str, speaker: str, flavor: Optional[str] = None, advantage: Optional[bool] = None,
    foundry: FoundryClient = None
) -> dict:
    """Roll dice in Foundry with optional advantage/disadvantage.

    advantage: True for advantage (roll twice, take higher), False for disadvantage
    (roll twice, take lower), None for normal roll.
    """
    # Handle advantage/disadvantage by rolling twice and selecting appropriately
    advantage_note = ""
    if advantage is not None:
        advantage_note = " (with advantage)" if advantage else " (with disadvantage)"

    result = await foundry.roll(formula, speaker=speaker, flavor=flavor)
    if advantage is not None:
        # Roll again for advantage/disadvantage comparison
        result2 = await foundry.roll(formula, speaker=speaker, flavor=f"{flavor or ''} (comparison roll)".strip())
        result["advantage"] = advantage
        result["advantage_result"] = result2
        # The LLM/system should interpret: for advantage, use max; for disadvantage, use min
        logger.info(f"[Roll] {formula} by {speaker}{advantage_note} → {result.get('result', 'unknown')} vs {result2.get('result', 'unknown')}")
    else:
        logger.info(f"[Roll] {formula} by {speaker} → {result.get('result', 'unknown')}")

    return {"type": "roll", "formula": formula, "speaker": speaker, "result": result}


async def execute_move_token(
    token_id: str, x: float, y: float, foundry: FoundryClient = None
) -> dict:
    """Move a token on the grid."""
    result = await foundry.update_entity(
        uuid=None, data={"token": {"x": x, "y": y}}, token_id=token_id
    )
    logger.info(f"[Move] Token {token_id} → ({x}, {y})")
    return {"type": "move_token", "token_id": token_id, "result": result}


async def execute_update_hp(
    actor_uuid: str, damage: int, hp_path: str = "hp.value", foundry: FoundryClient = None
) -> dict:
    """Apply damage (positive) or healing (negative) to an actor.

    The *hp_path* parameter comes from the validated Pydantic schema and
    has been sanitized to a simple dotted attribute name (no brackets,
    no arbitrary python expressions).

    If the system uses a different HP attribute path (e.g. "data.attributes.hp.value"
    for D&D 5e), the LLM can set it via the schema — otherwise the default
    "hp.value" is used.
    """
    if damage > 0:
        result = await foundry.decrease_attribute(hp_path, damage, actor_uuid)
        logger.info(f"[Damage] {actor_uuid} took {damage} damage")
    else:
        result = await foundry.increase_attribute(hp_path, abs(damage), actor_uuid)
        logger.info(f"[Heal] {actor_uuid} healed {-damage} HP")
    return {"type": "update_hp", "actor_uuid": actor_uuid, "damage": damage, "result": result}


async def execute_play_sound(
    sound_name: str, foundry: FoundryClient = None
) -> dict:
    """Play a sound effect in Foundry."""
    result = await foundry.play_sound(sound_name)
    logger.info(f"[Sound] {sound_name}")
    return {"type": "play_sound", "sound_name": sound_name, "result": result}


async def execute_play_music(
    playlist_name: str, volume: float = 0.5, foundry: FoundryClient = None
) -> dict:
    """Play background music from a Foundry playlist.

    The playlist_name is the name of a Foundry playlist that contains tracks.
    Volume is 0-1, with 0.5 as default (50% volume).
    """
    result = await foundry.play_playlist(playlist_name, volume)
    logger.info(f"[Music] Playing playlist '{playlist_name}' at {int(volume*100)}% volume")
    return {"type": "play_music", "playlist": playlist_name, "volume": volume, "result": result}


async def execute_whisper(
    player_id: str, message: str, foundry: FoundryClient = None
) -> dict:
    """Send a whispered message to a specific player (private message).

    The message is only visible to the specified player_id, not to the whole party.
    Use this for secret information, personal plots, or one-on-one dialogue.
    """
    result = await foundry.chat_message(message, speaker="GM", whisper=[player_id])
    logger.info(f"[Whisper] GM → {player_id}: {message[:60]}...")
    return {"type": "whisper", "player_id": player_id, "message": message, "result": result}


async def execute_switch_scene(
    scene_name: str, foundry: FoundryClient = None
) -> dict:
    """Change the current scene."""
    result = await foundry.set_active_scene(scene_name)
    logger.info(f"[Scene] Switched to {scene_name}")
    return {"type": "switch_scene", "scene_name": scene_name, "result": result}


async def execute_start_encounter(
    token_ids: list, foundry: FoundryClient = None, auto_roll_initiative: bool = True
) -> dict:
    """Begin combat and optionally auto-roll initiative for turn order.

    If auto_roll_initiative is True (default), initiative is rolled for all
    combatants automatically, ordering the turn tracker by initiative rolls.
    """
    result = await foundry.start_encounter(tokens=token_ids)
    logger.info(f"[Combat] Started encounter with {len(token_ids)} tokens")

    # Auto-roll initiative if requested
    if auto_roll_initiative:
        try:
            initiative_result = await foundry.roll_initiative()
            logger.info(f"[Combat] Auto-rolled initiative: {initiative_result}")
            result["initiative"] = initiative_result
        except Exception as e:
            logger.warning(f"[Combat] Failed to auto-roll initiative: {e}")

    return {"type": "start_encounter", "token_ids": token_ids, "result": result}


async def execute_end_encounter(foundry: FoundryClient = None) -> dict:
    """End combat."""
    result = await foundry.end_encounter()
    logger.info("[Combat] Ended encounter")
    return {"type": "end_encounter", "result": result}


async def execute_prompt_player(
    player_id: str, question: str, foundry: FoundryClient = None
) -> dict:
    """Ask a specific player for input.

    The *player_id* parameter is a Foundry user ID (not a display name),
    validated by the Pydantic schema.  It is used as the speaker for the
    chat message and as the whisper target so only that player sees it.
    """
    result = await foundry.chat_message(
        question, speaker=player_id, whisper=[player_id]
    )
    logger.info(f"[Prompt] {question}")
    return {"type": "prompt_player", "player_id": player_id, "result": result}


async def execute_cast_spell(
    actor_uuid: str, spell_name: str, spell_level: int, foundry: FoundryClient = None
) -> dict:
    """Cast a spell and manage spell slots.

    Spell slots are automatically decremented based on spell level.
    For cantrips (level 0), no spell slots are consumed.
    """
    result = await foundry.use_spell_slot(actor_uuid, spell_level)
    logger.info(f"[Spell] {spell_name} (level {spell_level}) cast by {actor_uuid}")
    return {"type": "cast_spell", "spell": spell_name, "level": spell_level, "result": result}


async def execute_use_action(
    actor_uuid: str, action_type: str, foundry: FoundryClient = None
) -> dict:
    """Track and consume an action in combat.

    action_type can be 'action', 'bonus_action', 'reaction', or 'movement'.
    This helps manage action economy during combat.
    """
    result = await foundry.track_action(actor_uuid, action_type)
    logger.info(f"[Action] {action_type} consumed by {actor_uuid}")
    return {"type": "use_action", "action_type": action_type, "result": result}


# Action handler registry
ACTION_HANDLERS = {
    "narrate": execute_narrate,
    "speak": execute_speak,
    "roll": execute_roll,
    "move_token": execute_move_token,
    "update_hp": execute_update_hp,
    "play_sound": execute_play_sound,
    "play_music": execute_play_music,
    "whisper": execute_whisper,
    "switch_scene": execute_switch_scene,
    "start_encounter": execute_start_encounter,
    "end_encounter": execute_end_encounter,
    "prompt_player": execute_prompt_player,
    "cast_spell": execute_cast_spell,
    "use_action": execute_use_action,
}
