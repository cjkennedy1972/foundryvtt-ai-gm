"""
Action executors — each function executes one type of GM action in FoundryVTT.

Each executor receives *validated* arguments from the dispatcher (Pydantic
schemas have already ensured correct types, ranges, and field names).
"""

import asyncio
import logging
from typing import Optional, Any

from foundry.client import FoundryClient

logger = logging.getLogger(__name__)

# Injected at startup by main.py; remains None when TTS is disabled.
_tts_service: Optional[Any] = None       # TTSService | None
_npc_registry: Optional[Any] = None      # NPCRegistry | None
_tts_volume: float = 0.8


def configure_tts(tts_service, npc_registry, volume: float = 0.8):
    """Wire TTS into the executor module (called once at startup)."""
    global _tts_service, _npc_registry, _tts_volume
    _tts_service = tts_service
    _npc_registry = npc_registry
    _tts_volume = volume


async def _play_tts(audio_url: str, foundry: FoundryClient):
    """Trigger Foundry to play a TTS audio URL for all clients."""
    js = (
        f"AudioHelper.play({{src: {audio_url!r}, volume: {_tts_volume}, loop: false}}, true)"
    )
    try:
        await foundry.execute_js(js)
    except Exception as e:
        logger.warning(f"[TTS] AudioHelper.play failed: {e}")


async def execute_narrate(text: str, foundry: FoundryClient) -> dict:
    """Send narration as GM in Foundry chat, then play TTS audio."""
    result = await foundry.chat_message(
        text, speaker=foundry._get_speaker_name(), whisper=[]
    )
    logger.info(f"[Narrate] {text[:80]}...")

    if _tts_service is not None:
        asyncio.create_task(_narrate_tts(text, foundry))

    return {"type": "narrate", "result": result}


async def _narrate_tts(text: str, foundry: FoundryClient):
    try:
        url = await _tts_service.narrate(text)
        if url:
            await _play_tts(url, foundry)
    except Exception as e:
        logger.warning(f"[TTS] Narration failed: {e}")


async def execute_speak(
    npc_name: str, text: str, whisper_to: Optional[str] = None, foundry: FoundryClient = None
) -> dict:
    """Speak as an NPC in Foundry chat, then play TTS audio with NPC-specific voice."""
    whisper_list = [whisper_to] if whisper_to else []
    result = await foundry.chat_message(text, speaker=npc_name, whisper=whisper_list)
    whisper_note = f" (whisper to {whisper_to})" if whisper_to else ""
    logger.info(f"[{npc_name}{whisper_note}] {text[:80]}...")

    if _tts_service is not None:
        npc_record = _npc_registry.get_npc_by_name(npc_name) if _npc_registry else None
        asyncio.create_task(_speak_tts(text, npc_name, npc_record, foundry))

    return {"type": "speak", "npc": npc_name, "result": result}


async def _speak_tts(text: str, npc_name: str, npc_record, foundry: FoundryClient):
    try:
        url = await _tts_service.speak(text, npc_name, npc_record)
        if url:
            await _play_tts(url, foundry)
    except Exception as e:
        logger.warning(f"[TTS] NPC speech failed for {npc_name}: {e}")


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
    sound_name: str, volume: float = 0.5, foundry: FoundryClient = None
) -> dict:
    """Play a sound effect in Foundry."""
    result = await foundry.play_sound(sound_name, volume=volume)
    logger.info(f"[Sound] {sound_name} (volume {volume})")
    return {"type": "play_sound", "sound_name": sound_name, "volume": volume, "result": result}


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
    """Generate a new combat encounter and deploy monsters to the active Foundry scene."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        encounter = gen.generate_encounter(party_level, party_size)

        logger.info(f"[Procedural] Generated encounter: {encounter.get('name', 'Unknown')} ({encounter.get('difficulty', 'unknown')})")

        result = {
            "type": "generate_encounter",
            "encounter": {
                "name": encounter.get("name", "Unknown Encounter"),
                "difficulty": encounter.get("difficulty", "unknown"),
                "description": encounter.get("description", ""),
                "monsters": encounter.get("monsters", []),
                "environment": encounter.get("environment", ""),
            }
        }

        if foundry and foundry.is_connected:
            from campaign.monster_actor import ensure_monster_actor
            placed_tokens = []
            monsters = encounter.get("monsters", [])
            for i, monster in enumerate(monsters):
                monster_name = monster.get("name", f"Monster {i+1}")
                cr = monster.get("cr", 1)
                hp = monster.get("hp", max(1, int(cr) * 7 + 3))
                ac = monster.get("ac", 10 + min(int(cr), 5))
                count = monster.get("count", 1)

                # Resolve or import actor — tries world lookup → compendium import
                # → placeholder with compendium portrait art as fallback.
                actor_uuid = await ensure_monster_actor(
                    foundry, monster_name, cr=cr, hp=hp, ac=ac
                )

                # Place tokens spread across the scene
                for j in range(count):
                    x = 200 + (i * 150) + (j * 50)
                    y = 200 + (j * 100)
                    token_result = await foundry.place_token(monster_name, x=x, y=y, disposition=-1)
                    if token_result and "error" not in token_result:
                        placed_tokens.append(token_result.get("id", ""))

            # Start encounter if tokens were placed
            if placed_tokens:
                await foundry.start_encounter(placed_tokens)
                logger.info(f"[Procedural] Placed {len(placed_tokens)} monster tokens and started encounter")

            result["placed_tokens"] = placed_tokens
            result["deployed_to_foundry"] = len(placed_tokens) > 0

        return result
    except Exception as e:
        logger.error(f"[Procedural] Encounter generation failed: {e}")
        return {"type": "generate_encounter", "error": str(e)}


async def execute_generate_treasure(
    cr: float, rarity_preference: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate loot and treasure, creating Foundry items in a loot journal entry."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        treasure = gen.generate_treasure(cr)

        logger.info(f"[Procedural] Generated treasure worth {treasure.get('total_value_gp', 0)}gp")

        result = {
            "type": "generate_treasure",
            "treasure": {
                "items": treasure.get("items", []),
                "total_value_gp": treasure.get("total_value_gp", 0),
                "gold_coins": treasure.get("gold_coins", 0),
            }
        }

        if foundry and foundry.is_connected:
            items = treasure.get("items", [])
            gold = treasure.get("gold_coins", 0)
            total_gp = treasure.get("total_value_gp", 0)

            content = (
                f"<h2>Loot Found</h2>"
                f"<p>Total value: {total_gp} gp</p>"
                f"<ul>{''.join(f'<li>{item}</li>' for item in items)}"
                f"{'<li>' + str(gold) + ' gold coins</li>' if gold else ''}</ul>"
            )
            journal_data = {
                "name": f"Treasure (CR {cr})",
                "pages": [{"name": "Loot", "type": "text", "text": {"content": content, "format": 1}}],
            }
            journal_result = await foundry.create_entity("JournalEntry", journal_data)
            result["journal_uuid"] = (journal_result or {}).get("uuid", "")
            result["deployed_to_foundry"] = bool(result["journal_uuid"])
            logger.info(f"[Procedural] Created loot journal entry: {result['journal_uuid']}")

        return result
    except Exception as e:
        logger.error(f"[Procedural] Treasure generation failed: {e}")
        return {"type": "generate_treasure", "error": str(e)}


async def execute_generate_npc(
    role: Optional[str] = None, faction: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate a new NPC and create a Foundry actor + token on the current scene."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        npc = gen.generate_npc()

        name = npc.get("name", "Unknown NPC")
        logger.info(f"[Procedural] Generated NPC: {name} ({npc.get('class_name', 'unknown')})")

        result = {
            "type": "generate_npc",
            "npc": {
                "name": name,
                "race": npc.get("race", "Human"),
                "class": npc.get("class_name", "Commoner"),
                "level": npc.get("level", 1),
                "alignment": npc.get("alignment", "Neutral"),
                "description": npc.get("description", ""),
            }
        }

        if foundry and foundry.is_connected:
            level = npc.get("level", 1)
            hp = max(1, level * 4)
            actor_data = {
                "name": name,
                "type": "npc",
                "system": {
                    "details": {
                        "alignment": npc.get("alignment", "Neutral"),
                        "biography": {"value": npc.get("description", "")},
                    },
                    "attributes": {
                        "hp": {"value": hp, "max": hp},
                    },
                },
            }
            actor_result = await foundry.create_entity("Actor", actor_data)
            actor_uuid = (actor_result or {}).get("uuid", "")

            # Place token offset from center so multiple NPCs don't stack
            npc_index = result.get("_npc_index", 0)
            token_result = await foundry.place_token(name, x=400 + npc_index * 100, y=400, disposition=0)
            token_id = (token_result or {}).get("id", "") if "error" not in (token_result or {}) else ""

            result["npc"]["actor_uuid"] = actor_uuid
            result["npc"]["token_id"] = token_id
            result["deployed_to_foundry"] = bool(actor_uuid)
            logger.info(f"[Procedural] Created NPC actor {actor_uuid}, token {token_id}")

        return result
    except Exception as e:
        logger.error(f"[Procedural] NPC generation failed: {e}")
        return {"type": "generate_npc", "error": str(e)}


async def execute_generate_quest(
    theme: Optional[str] = None, difficulty: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate a new quest and create a Foundry JournalEntry for it."""
    try:
        from procedural.generator import ProceduralGenerator
        gen = ProceduralGenerator()
        quest = gen.generate_quest()

        title = quest.get("title", "Unknown Quest")
        logger.info(f"[Procedural] Generated quest: {title} ({quest.get('difficulty', 'unknown')})")

        result = {
            "type": "generate_quest",
            "quest": {
                "title": title,
                "objective": quest.get("objective", ""),
                "difficulty": quest.get("difficulty", "medium"),
                "reward": quest.get("reward", ""),
                "objectives": quest.get("objectives", []),
            }
        }

        if foundry and foundry.is_connected:
            objectives = quest.get("objectives", [])
            obj_html = "".join(f"<li>{o}</li>" for o in objectives) if objectives else f"<li>{quest.get('objective', '')}</li>"
            content = (
                f"<h2>{title}</h2>"
                f"<h3>Objective</h3><p>{quest.get('objective', '')}</p>"
                f"<h3>Tasks</h3><ul>{obj_html}</ul>"
                f"<h3>Reward</h3><p>{quest.get('reward', '')}</p>"
                f"<p><em>Difficulty: {quest.get('difficulty', 'medium')}</em></p>"
            )
            journal_data = {
                "name": title,
                "pages": [{"name": "Quest Details", "type": "text", "text": {"content": content, "format": 1}}],
            }
            journal_result = await foundry.create_entity("JournalEntry", journal_data)
            journal_uuid = (journal_result or {}).get("uuid", "")
            result["quest"]["journal_uuid"] = journal_uuid
            result["deployed_to_foundry"] = bool(journal_uuid)
            logger.info(f"[Procedural] Created quest journal entry: {journal_uuid}")

        return result
    except Exception as e:
        logger.error(f"[Procedural] Quest generation failed: {e}")
        return {"type": "generate_quest", "error": str(e)}


async def execute_place_walls(
    walls: list, clear_existing: bool = False, foundry: FoundryClient = None
) -> dict:
    """Place wall segments on the current Foundry scene.

    Each wall dict: {c:[x0,y0,x1,y1], move:20, sense:20, door:0, ds:0}
    move/sense/sound: 0=none, 10=limited, 20=normal, 30=ethereal, 40=impassable
    door: 0=wall, 1=door, 2=secret door
    ds (door state): 0=closed, 1=open, 2=locked
    """
    if clear_existing:
        try:
            await foundry.clear_canvas_layer("walls")
            logger.info("[Walls] Cleared existing walls")
        except Exception as e:
            logger.warning(f"[Walls] Failed to clear existing walls: {e}")

    result = await foundry.canvas_create("walls", walls)
    logger.info(f"[Walls] Placed {len(walls)} wall segments")
    return {"type": "place_walls", "count": len(walls), "result": result}


async def execute_place_lights(
    lights: list, clear_existing: bool = False, foundry: FoundryClient = None
) -> dict:
    """Place ambient light sources on the current scene.

    Each light dict: {x, y, config:{bright:30, dim:60, color:'#ff4400', alpha:0.5}}
    bright/dim are in Foundry distance units (not pixels).
    """
    if clear_existing:
        try:
            await foundry.clear_canvas_layer("lights")
            logger.info("[Lights] Cleared existing lights")
        except Exception as e:
            logger.warning(f"[Lights] Failed to clear existing lights: {e}")

    result = await foundry.canvas_create("lights", lights)
    logger.info(f"[Lights] Placed {len(lights)} light sources")
    return {"type": "place_lights", "count": len(lights), "result": result}


async def execute_place_sounds(
    sounds: list, clear_existing: bool = False, foundry: FoundryClient = None
) -> dict:
    """Place ambient sound emitters on the current scene."""
    if clear_existing:
        try:
            await foundry.clear_canvas_layer("sounds")
        except Exception as e:
            logger.warning(f"[Sounds] Failed to clear existing sounds: {e}")

    result = await foundry.canvas_create("sounds", sounds)
    logger.info(f"[Sounds] Placed {len(sounds)} sound emitters")
    return {"type": "place_sounds", "count": len(sounds), "result": result}


async def execute_place_token(
    actor_name: str, x: float, y: float,
    disposition: int = 0, hidden: bool = False,
    foundry: FoundryClient = None
) -> dict:
    """Place an actor's token on the current scene."""
    result = await foundry.place_token(actor_name, x, y, disposition=disposition, hidden=hidden)
    logger.info(f"[Token] Placed '{actor_name}' at ({x}, {y}) disposition={disposition}")
    return {"type": "place_token", "actor": actor_name, "x": x, "y": y, "result": result}


async def execute_configure_scene(
    darkness: Optional[float] = None,
    global_illumination: Optional[bool] = None,
    fog_exploration: Optional[bool] = None,
    tokenVision: Optional[bool] = None,
    grid_size: Optional[int] = None,
    scene_name: Optional[str] = None,
    foundry: FoundryClient = None,
) -> dict:
    """Update scene-level settings (darkness, fog, vision, grid)."""
    updates = {}
    if darkness is not None:
        updates["darkness"] = darkness
    if global_illumination is not None:
        updates["globalLight"] = global_illumination
    if fog_exploration is not None:
        updates["fogExploration"] = fog_exploration
    if tokenVision is not None:
        updates["tokenVision"] = tokenVision
    if grid_size is not None:
        updates["grid"] = {"size": grid_size}

    if not updates:
        return {"type": "configure_scene", "result": "no changes"}

    result = await foundry.configure_scene(updates, scene_name=scene_name)
    logger.info(f"[Scene] Configured: {list(updates.keys())}")
    return {"type": "configure_scene", "updates": updates, "result": result}


async def execute_setup_scene(
    scene_name: Optional[str] = None,
    walls: Optional[list] = None,
    lights: Optional[list] = None,
    sounds: Optional[list] = None,
    tokens: Optional[list] = None,
    darkness: Optional[float] = None,
    fog_exploration: Optional[bool] = None,
    global_illumination: Optional[bool] = None,
    tokenVision: Optional[bool] = None,
    clear_walls: bool = False,
    clear_lights: bool = False,
    narrate: Optional[str] = None,
    foundry: FoundryClient = None,
) -> dict:
    """Full scene setup — walls, lights, sounds, tokens, and scene config in sequence."""
    results = {}

    # Switch to named scene first if provided
    if scene_name:
        try:
            await foundry.set_active_scene(scene_name)
            results["scene_switch"] = "ok"
            logger.info(f"[Setup] Switched to scene: {scene_name}")
        except Exception as e:
            logger.warning(f"[Setup] Could not switch to scene '{scene_name}': {e}")

    # Configure scene-level settings
    scene_updates = {}
    if darkness is not None:
        scene_updates["darkness"] = darkness
    if global_illumination is not None:
        scene_updates["globalLight"] = global_illumination
    if fog_exploration is not None:
        scene_updates["fogExploration"] = fog_exploration
    if tokenVision is not None:
        scene_updates["tokenVision"] = tokenVision
    if scene_updates:
        try:
            await foundry.configure_scene(scene_updates)
            results["scene_config"] = scene_updates
            logger.info(f"[Setup] Scene config: {scene_updates}")
        except Exception as e:
            logger.warning(f"[Setup] Scene config failed: {e}")
            results["scene_config_error"] = str(e)

    # Place walls
    if walls:
        try:
            if clear_walls:
                await foundry.clear_canvas_layer("walls")
            wall_result = await foundry.canvas_create("walls", walls)
            results["walls"] = len(walls)
            logger.info(f"[Setup] Placed {len(walls)} walls")
        except Exception as e:
            logger.warning(f"[Setup] Wall placement failed: {e}")
            results["walls_error"] = str(e)

    # Place lights
    if lights:
        try:
            if clear_lights:
                await foundry.clear_canvas_layer("lights")
            await foundry.canvas_create("lights", lights)
            results["lights"] = len(lights)
            logger.info(f"[Setup] Placed {len(lights)} lights")
        except Exception as e:
            logger.warning(f"[Setup] Light placement failed: {e}")
            results["lights_error"] = str(e)

    # Place sounds
    if sounds:
        try:
            await foundry.canvas_create("sounds", sounds)
            results["sounds"] = len(sounds)
            logger.info(f"[Setup] Placed {len(sounds)} sounds")
        except Exception as e:
            logger.warning(f"[Setup] Sound placement failed: {e}")
            results["sounds_error"] = str(e)

    # Place tokens
    if tokens:
        placed = 0
        for tok in tokens:
            actor_name = tok.get("actor_name") or tok.get("name")
            x = tok.get("x", 0)
            y = tok.get("y", 0)
            disposition = tok.get("disposition", 0)
            hidden = tok.get("hidden", False)
            if actor_name:
                try:
                    await foundry.place_token(actor_name, x, y, disposition=disposition, hidden=hidden)
                    placed += 1
                except Exception as e:
                    logger.warning(f"[Setup] Failed to place token '{actor_name}': {e}")
        results["tokens"] = placed
        logger.info(f"[Setup] Placed {placed}/{len(tokens)} tokens")

    # Narrate after setup
    if narrate:
        try:
            await foundry.chat_message(narrate, speaker=foundry._get_speaker_name())
            results["narrated"] = True
        except Exception as e:
            logger.warning(f"[Setup] Narration failed: {e}")

    logger.info(f"[Setup] Scene setup complete: {results}")
    return {"type": "setup_scene", "results": results, "success": True}


async def execute_generate_map(
    prompt: str,
    scene_name: str,
    style: str = "dungeon",
    size: str = "medium",
    switch_to_scene: bool = True,
    app_state=None,
    foundry: FoundryClient = None,
) -> dict:
    """Generate an AI battle map via ComfyUI and create a Foundry scene from it."""
    if not app_state or not hasattr(app_state, "map_generator") or not app_state.map_generator:
        return {"type": "generate_map", "error": "Map generator not available (ComfyUI required)"}

    import asyncio
    from pathlib import Path

    size_map = {"small": (1024, 768), "medium": (1536, 1152), "large": (2048, 1536)}
    width, height = size_map.get(size, (1536, 1152))

    output_dir = Path(getattr(app_state, "map_output_dir", "/tmp/ai-gm-maps"))
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"[MapGen] Generating '{scene_name}': {prompt[:80]}")
    try:
        gen_result = await app_state.map_generator.generate_map(
            prompt=prompt,
            output_dir=output_dir,
            width=width,
            height=height,
            style=style,
        )
    except Exception as e:
        logger.error(f"[MapGen] ComfyUI generation failed: {e}")
        return {"type": "generate_map", "error": str(e)}

    if gen_result.get("status") != "success" or not gen_result.get("output_file"):
        return {"type": "generate_map", "error": gen_result.get("error", "generation failed")}

    output_file = Path(gen_result["output_file"])

    # Upload to Foundry's data directory
    foundry_path = "worlds/maps"
    filename = output_file.name
    try:
        file_bytes = output_file.read_bytes()
        upload_result = await foundry.upload_file(
            file_bytes=file_bytes,
            path=foundry_path,
            filename=filename,
            mime_type="image/png",
        )
        background_src = upload_result.get("path", f"{foundry_path}/{filename}")
    except Exception as e:
        logger.warning(f"[MapGen] Upload failed, using local path: {e}")
        background_src = str(output_file)

    # Create the Foundry scene
    scene_data = {
        "name": scene_name,
        "background": {"src": background_src},
        "width": width,
        "height": height,
        "grid": {"size": 70},
        "fogExploration": True,
        "tokenVision": True,
        "darkness": 0.0,
    }
    try:
        create_result = await foundry.create_entity("Scene", scene_data)
        scene_id = create_result.get("data", {}).get("_id") or create_result.get("id")
        logger.info(f"[MapGen] Scene '{scene_name}' created (id={scene_id})")

        if switch_to_scene:
            await asyncio.sleep(0.5)  # let Foundry finish creating the scene
            await foundry.set_active_scene(scene_name)

        return {
            "type": "generate_map",
            "scene_name": scene_name,
            "background": background_src,
            "dimensions": {"width": width, "height": height},
            "success": True,
        }
    except Exception as e:
        logger.error(f"[MapGen] Scene creation failed: {e}")
        return {"type": "generate_map", "error": str(e), "background": background_src}


async def execute_execute_js(
    code: str,
    description: Optional[str] = None,
    foundry: FoundryClient = None,
) -> dict:
    """Execute arbitrary JavaScript in the Foundry client.

    Disabled unless ``allow_execute_js`` is set. This action is reachable from
    player chat via the LLM, so an always-on bridge to arbitrary Foundry JS lets
    a prompt-injected message run destructive scripts against the world.
    """
    from config import settings as _settings
    desc = description or code[:60]
    if not getattr(_settings, "allow_execute_js", False):
        logger.warning(f"[JS] Blocked execute_js (allow_execute_js=false): {desc}")
        return {
            "type": "execute_js",
            "description": desc,
            "success": False,
            "error": "execute_js is disabled. Set ALLOW_EXECUTE_JS=true to enable arbitrary Foundry JavaScript.",
        }
    logger.info(f"[JS] Executing: {desc}")
    result = await foundry.execute_js(code)
    return {"type": "execute_js", "description": desc, "result": result}


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
    # Scene building
    "place_walls": execute_place_walls,
    "place_lights": execute_place_lights,
    "place_sounds": execute_place_sounds,
    "place_token": execute_place_token,
    "configure_scene": execute_configure_scene,
    "setup_scene": execute_setup_scene,
    "generate_map": execute_generate_map,
    "execute_js": execute_execute_js,
}
