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


async def execute_skill_check(
    actor_uuid: str, skill: str, dc: int, reason: Optional[str] = None,
    advantage: Optional[bool] = None, foundry: FoundryClient = None
) -> dict:
    """Request a skill check from the player.

    The player will roll and the result is compared against the DC.
    """
    reason_text = f" ({reason})" if reason else ""
    advantage_text = " with advantage" if advantage else (" with disadvantage" if advantage is False else "")
    logger.info(f"[Skill Check] {skill} DC {dc}{advantage_text}{reason_text}")

    result = await foundry.request_skill_check(
        actor_uuid, skill, dc, reason=reason, advantage=advantage
    )
    return {
        "type": "skill_check",
        "skill": skill,
        "dc": dc,
        "advantage": advantage,
        "result": result,
    }


async def execute_apply_condition(
    actor_uuid: str, condition: str, duration: Optional[str] = None,
    foundry: FoundryClient = None
) -> dict:
    """Apply a condition to a creature.

    Conditions can last for specific durations or until removed.
    """
    result = await foundry.apply_condition(actor_uuid, condition, duration)
    logger.info(f"[Condition] Applied {condition} to {actor_uuid} ({duration or 'until removed'})")
    return {
        "type": "apply_condition",
        "condition": condition,
        "duration": duration,
        "result": result,
    }


async def execute_opportunity_attack(
    attacker_uuid: str, target_uuid: str, reason: Optional[str] = None,
    foundry: FoundryClient = None
) -> dict:
    """Trigger an opportunity attack when a creature leaves an enemy's reach.

    Opportunity attacks are reactions that occur when a hostile creature moves
    away from you while you can see it.
    """
    reason_str = f" ({reason})" if reason else ""
    logger.info(f"[Opportunity Attack] {attacker_uuid} attacks {target_uuid}{reason_str}")

    result = await foundry.opportunity_attack(attacker_uuid, target_uuid)
    return {
        "type": "opportunity_attack",
        "attacker": attacker_uuid,
        "target": target_uuid,
        "reason": reason,
        "result": result,
    }


async def execute_tactical_analysis(
    actor_uuid: str, include_recommendations: bool = True,
    foundry: FoundryClient = None
) -> dict:
    """Perform tactical analysis of the current battlefield.

    Analyzes flanking positions, reach, cover, and enemy positioning
    to provide tactical recommendations.
    """
    from combat.mechanics import CombatMechanics

    mechanics = CombatMechanics()
    analysis = mechanics.get_tactical_analysis(actor_uuid, [], [])

    recommendations = analysis.get_recommendations() if include_recommendations else []

    logger.info(f"[Tactical Analysis] {actor_uuid}: {len(analysis.enemies_in_range)} enemies in range")

    return {
        "type": "tactical_analysis",
        "actor": actor_uuid,
        "flanking_enemies": analysis.flanking_enemies,
        "enemies_in_range": analysis.enemies_in_range,
        "opportunity_threats": analysis.opportunity_attack_threats,
        "recommendations": recommendations,
    }


async def execute_set_weather(
    weather: str, app_state = None
) -> dict:
    """Set weather and atmosphere."""
    if not app_state or not hasattr(app_state, 'ambient_manager'):
        return {"type": "set_weather", "error": "Ambient manager not available"}

    from immersion.ambient import WeatherType
    try:
        weather_type = WeatherType(weather.lower())
        result = app_state.ambient_manager.set_weather(weather_type)
        logger.info(f"[Weather] Set to {weather}")
        return {"type": "set_weather", "result": result}
    except ValueError:
        logger.error(f"[Weather] Invalid weather type: {weather}")
        return {"type": "set_weather", "error": f"Unknown weather type: {weather}"}


async def execute_set_time(
    time: str, app_state = None
) -> dict:
    """Set time of day for atmosphere."""
    if not app_state or not hasattr(app_state, 'ambient_manager'):
        return {"type": "set_time", "error": "Ambient manager not available"}

    from immersion.ambient import TimeOfDay
    try:
        time_type = TimeOfDay(time.lower())
        result = app_state.ambient_manager.set_time(time_type)
        logger.info(f"[Time] Set to {time}")
        return {"type": "set_time", "result": result}
    except ValueError:
        logger.error(f"[Time] Invalid time type: {time}")
        return {"type": "set_time", "error": f"Unknown time: {time}"}


async def execute_apply_token_effect(
    token_id: str, effect_type: str, effect_name: str, duration: Optional[int] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Apply visual effects to tokens."""
    if not app_state or not hasattr(app_state, 'effects_manager'):
        return {"type": "apply_token_effect", "error": "Effects manager not available"}

    if effect_type == "condition":
        result = app_state.effects_manager.apply_condition_visual(token_id, effect_name, duration)
    elif effect_type == "aura":
        result = app_state.effects_manager.apply_aura(token_id, effect_name, duration)
    else:
        logger.warning(f"[Effect] Unknown effect type: {effect_type}")
        return {"type": "apply_token_effect", "error": f"Unknown effect type: {effect_type}"}

    logger.info(f"[Effect] Applied {effect_name} ({effect_type}) to {token_id}")
    return {"type": "apply_token_effect", "result": result}


async def execute_update_vision(
    token_id: str, vision_range: float, has_light: bool = False,
    light_radius: Optional[float] = None, app_state = None, foundry: FoundryClient = None
) -> dict:
    """Update vision and fog of war."""
    if not app_state or not hasattr(app_state, 'vision_manager'):
        return {"type": "update_vision", "error": "Vision manager not available"}

    result = app_state.vision_manager.set_vision_range(token_id, vision_range)

    if has_light and light_radius:
        light_result = app_state.vision_manager.apply_light_source(token_id, light_radius)
        result["light"] = light_result
        logger.info(f"[Vision] {token_id}: vision {vision_range}ft, light {light_radius}ft")
    else:
        logger.info(f"[Vision] {token_id}: vision {vision_range}ft")

    return {"type": "update_vision", "result": result}


async def execute_generate_encounter(
    party_level: int, party_size: int, environment: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate a new combat encounter."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        encounter = gen.generate_encounter(party_level, party_size)

        logger.info(f"[Procedural] Generated encounter: {encounter.get('name', 'Unknown')} ({encounter.get('difficulty', 'unknown')})")

        return {
            "type": "generate_encounter",
            "encounter": {
                "name": encounter.get("name", "Unknown Encounter"),
                "difficulty": encounter.get("difficulty", "unknown"),
                "description": encounter.get("description", ""),
                "monsters": encounter.get("monsters", []),
                "environment": encounter.get("environment", ""),
            }
        }
    except Exception as e:
        logger.error(f"[Procedural] Encounter generation failed: {e}")
        return {"type": "generate_encounter", "error": str(e)}


async def execute_generate_treasure(
    cr: float, rarity_preference: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate loot and treasure."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        treasure = gen.generate_treasure(cr)

        logger.info(f"[Procedural] Generated treasure worth {treasure.get('total_value_gp', 0)}gp")

        return {
            "type": "generate_treasure",
            "treasure": {
                "items": treasure.get("items", []),
                "total_value_gp": treasure.get("total_value_gp", 0),
                "gold_coins": treasure.get("gold_coins", 0),
            }
        }
    except Exception as e:
        logger.error(f"[Procedural] Treasure generation failed: {e}")
        return {"type": "generate_treasure", "error": str(e)}


async def execute_generate_npc(
    role: Optional[str] = None, faction: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate a new NPC."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        npc = gen.generate_npc()

        logger.info(f"[Procedural] Generated NPC: {npc.get('name', 'Unknown')} ({npc.get('class_name', 'unknown')})")

        return {
            "type": "generate_npc",
            "npc": {
                "name": npc.get("name", "Unknown NPC"),
                "race": npc.get("race", "Human"),
                "class": npc.get("class_name", "Commoner"),
                "level": npc.get("level", 1),
                "alignment": npc.get("alignment", "Neutral"),
                "description": npc.get("description", ""),
            }
        }
    except Exception as e:
        logger.error(f"[Procedural] NPC generation failed: {e}")
        return {"type": "generate_npc", "error": str(e)}


async def execute_generate_quest(
    theme: Optional[str] = None, difficulty: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate a new quest."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        quest = gen.generate_quest()

        logger.info(f"[Procedural] Generated quest: {quest.get('title', 'Unknown')} ({quest.get('difficulty', 'unknown')})")

        return {
            "type": "generate_quest",
            "quest": {
                "title": quest.get("title", "Unknown Quest"),
                "objective": quest.get("objective", ""),
                "difficulty": quest.get("difficulty", "medium"),
                "reward": quest.get("reward", ""),
                "objectives": quest.get("objectives", []),
            }
        }
    except Exception as e:
        logger.error(f"[Procedural] Quest generation failed: {e}")
        return {"type": "generate_quest", "error": str(e)}


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
    "skill_check": execute_skill_check,
    "apply_condition": execute_apply_condition,
    "opportunity_attack": execute_opportunity_attack,
    "tactical_analysis": execute_tactical_analysis,
    "set_weather": execute_set_weather,
    "set_time": execute_set_time,
    "apply_token_effect": execute_apply_token_effect,
    "update_vision": execute_update_vision,
    "generate_encounter": execute_generate_encounter,
    "generate_treasure": execute_generate_treasure,
    "generate_npc": execute_generate_npc,
    "generate_quest": execute_generate_quest,
}
