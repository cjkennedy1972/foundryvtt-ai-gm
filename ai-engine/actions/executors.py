"""
Action executors — each function executes one type of GM action in FoundryVTT.

Each executor receives *validated* arguments from the dispatcher (Pydantic
schemas have already ensured correct types, ranges, and field names).
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Any

from foundry.client import FoundryClient
from config import settings
from tts import playback as tts_playback
from utils.tasks import spawn

logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    """Raised when an action cannot be executed due to missing dependencies."""
    pass


def _require(condition: bool, message: str):
    """FAIL-FAST: Raise ExecutionError if condition is False."""
    if not condition:
        raise ExecutionError(message)


def _require_foundry_connected(foundry: FoundryClient):
    """Ensure Foundry client is connected before executing action."""
    _require(
        foundry and foundry.is_connected,
        "Foundry is not connected — cannot execute action. Check relay connection."
    )


async def _notify_scene_change(app_state, scene_name: str) -> None:
    """Tell SceneAwareness the scene changed after a programmatic switch.

    The scene-switch actions activate the Foundry scene directly, so we update
    awareness here rather than waiting on a relay scene-event round-trip — that
    event is not reliably emitted for scene.activate() from the headless client
    (and its payload key differs from what the handler reads), which left the
    scene cache empty all session. on_scene_change is idempotent (it no-ops when
    already on the scene), so this is safe to call on every switch.
    """
    # New scene, new canvas: forget which NPCs were confirmed present.
    reset_action_caches()
    if not (app_state and scene_name and getattr(app_state, "scene_awareness", None)):
        return
    try:
        await app_state.scene_awareness.on_scene_change(scene_name)
    except Exception as e:
        logger.debug(f"[Scene] on_scene_change notify failed: {e}")


async def execute_narrate(text: str, foundry: FoundryClient) -> dict:
    """Send narration as GM in Foundry chat, then play TTS audio."""
    result = await foundry.chat_message(
        text, speaker=foundry._get_speaker_name(), whisper=[]
    )
    logger.info(f"[Narrate] {text[:80]}...")

    if tts_playback.is_active():
        spawn(tts_playback.narrate(text, foundry))

    return {"type": "narrate", "result": result}


async def execute_speak(
    npc_name: str, text: str, whisper_to: Optional[str] = None, foundry: FoundryClient = None
) -> dict:
    """Speak as an NPC in Foundry chat, then play TTS audio with NPC-specific voice.

    Refuses to voice a player-owned actor: letting the LLM narrate dialogue
    "as" a PC (1) violates the system prompt's own rule against speaking for
    players and (2) registers that name in ChatListener's AI-controlled-speaker
    set, which then makes the echo guard treat the player's own real chat
    messages (posted under their character's name) as AI echoes and silently
    drop them.
    """
    if await _is_player_character(npc_name, foundry):
        logger.warning(f"[Speak] Refusing to voice player-owned actor '{npc_name}'")
        return {
            "type": "speak", "npc": npc_name, "success": False,
            "error": f"'{npc_name}' is a player character — the GM can't speak for them.",
        }
    whisper_list = [whisper_to] if whisper_to else []
    result = await foundry.chat_message(text, speaker=npc_name, whisper=whisper_list)
    whisper_note = f" (whisper to {whisper_to})" if whisper_to else ""
    logger.info(f"[{npc_name}{whisper_note}] {text[:80]}...")

    # Scene presence: a speaking NPC should be visible on the canvas, not a
    # disembodied chat voice. Runs in the background so dialogue isn't delayed.
    if not whisper_to:
        spawn(_ensure_npc_presence(npc_name, foundry))

    if tts_playback.is_active():
        npc_record = tts_playback.get_npc_record(npc_name)
        spawn(tts_playback.speak(text, npc_name, npc_record, foundry))

    return {"type": "speak", "npc": npc_name, "result": result}


# npc_name(lower) -> loop-time of last presence check; avoids re-querying
# Foundry for every line of a multi-beat dialogue.
_npc_presence_checked: dict = {}
_PRESENCE_RECHECK_SECS = 120.0


def reset_action_caches() -> None:
    """Reset cross-scene/player caches after a world or scene change."""
    global _pc_names_cache_at, _pc_uuid_cache_at, _pc_uuid_cache
    _npc_presence_checked.clear()
    _pc_names_cache.clear()
    _pc_names_cache_at = 0.0
    _pc_uuid_cache_at = 0.0
    _pc_uuid_cache.clear()


async def _ensure_npc_presence(npc_name: str, foundry: FoundryClient):
    """Reveal the speaking NPC's token, or place one beside the party."""
    from foundry import scripts

    key = npc_name.strip().lower()
    now = asyncio.get_event_loop().time()
    if now - _npc_presence_checked.get(key, float("-inf")) < _PRESENCE_RECHECK_SECS:
        return
    _npc_presence_checked[key] = now
    try:
        res = await foundry.execute_js(scripts.ensure_npc_token(npc_name))
        r = res.get("result") if isinstance(res, dict) else None
        if isinstance(r, dict) and r.get("ok"):
            if r.get("revealed"):
                logger.info(f"[Presence] Revealed token for speaking NPC '{npc_name}'")
            elif r.get("placed"):
                logger.info(f"[Presence] Placed token for speaking NPC '{npc_name}' beside the party")
        else:
            reason = (r or {}).get("reason") if isinstance(r, dict) else res
            logger.debug(f"[Presence] '{npc_name}' not placed: {reason}")
    except Exception as e:
        logger.debug(f"[Presence] check failed for '{npc_name}': {e}")


def _advantage_formula(formula: str, advantage: Optional[bool]) -> str:
    """Rewrite a d20 formula for advantage/disadvantage as a single Foundry roll.

    "1d20+3" -> "2d20kh1+3" (advantage) or "2d20kl1+3" (disadvantage). Returns
    the formula unchanged when advantage is None or it isn't a leading dN term.
    """
    if advantage is None:
        return formula
    import re
    m = re.match(r"\s*(\d*)d(\d+)(.*)", (formula or "").strip(), re.IGNORECASE)
    if not m:
        return formula
    _, faces, rest = m.groups()
    keep = "kh1" if advantage else "kl1"
    return f"2d{faces}{keep}{rest}"


_pc_names_cache: set = set()
_pc_names_cache_at: float = 0.0


async def _is_player_character(name: str, foundry: FoundryClient) -> bool:
    """True if `name` is a player-owned actor (cached ~30s to avoid per-roll RPCs)."""
    global _pc_names_cache, _pc_names_cache_at
    if not name or foundry is None:
        return False
    import time as _t
    now = _t.monotonic()
    if not _pc_names_cache or now - _pc_names_cache_at > 30:
        try:
            actors = await foundry.get_actors(world_only=True)
            _pc_names_cache = {
                a.get("name", "").lower() for a in actors if a.get("has_player_owner")
            }
            _pc_names_cache_at = now
        except Exception:
            pass
    return name.strip().lower() in _pc_names_cache


async def execute_roll(
    formula: str, speaker: str, flavor: Optional[str] = None, advantage: Optional[bool] = None,
    foundry: FoundryClient = None
) -> dict:
    """Roll dice in Foundry with optional advantage/disadvantage.

    advantage: True for advantage (roll twice, take higher), False for disadvantage
    (roll twice, take lower), None for normal roll.
    """
    # In D&D the players roll their own dice — rolling for them removes the whole
    # point. If this roll is for a player character, defer it: prompt the player
    # to roll instead of auto-rolling. The GM still rolls for NPCs/monsters.
    if getattr(settings, "players_roll_own", True) and await _is_player_character(speaker, foundry):
        adv = "" if advantage is None else (
            " with **advantage**" if advantage else " with **disadvantage**"
        )
        why = f" — {flavor}" if flavor else ""
        await foundry.chat_message(
            f"🎲 **{speaker}**, roll `{formula}`{adv}{why}. "
            "(Roll from your sheet, or tell me your result.)",
            speaker="GM",
        )
        logger.info(f"[Roll] Deferred {formula} to player '{speaker}' (players roll their own dice)")
        return {
            "type": "roll", "formula": formula, "speaker": speaker,
            "deferred_to_player": True, "success": True,
        }

    # Advantage/disadvantage as ONE proper Foundry roll (2d20kh1 / 2d20kl1) so
    # Foundry keeps the correct die and the 3D dice addon animates a single
    # roll — rather than two separate silent rolls the addon can't reconcile.
    roll_formula = _advantage_formula(formula, advantage)
    adv_note = "" if advantage is None else (
        " (advantage)" if advantage else " (disadvantage)"
    )
    result = await foundry.roll(roll_formula, speaker=speaker, flavor=flavor)
    total = result.get("total") if isinstance(result, dict) else None
    logger.info(f"[Roll] {roll_formula} by {speaker}{adv_note} → {total if total is not None else result}")

    return {
        "type": "roll", "formula": roll_formula, "speaker": speaker,
        "advantage": advantage, "result": result,
    }


async def _resolve_token_id(identifier: str, foundry: FoundryClient) -> str:
    """Map a token id / actor uuid / name to a real scene token id.

    The LLM frequently passes an actor uuid (e.g. 'Actor.xxx') or a display name
    instead of the scene token id that move_token needs, which the relay rejects
    with 'Entity not found'. Resolve against the live scene tokens; return the
    original identifier unchanged if nothing matches so the error still surfaces.
    """
    ident = (identifier or "").strip()
    if not ident:
        return ident
    try:
        tokens = await foundry.get_scene_tokens()
    except Exception:
        return ident
    ident_l = ident.lower()
    short = ident.split(".")[-1].lower()
    # 1. Already a real token id — use as-is.
    for t in tokens:
        if str(t.get("id", "")) == ident:
            return ident
    # 2. Match by actor uuid (full or trailing id segment).
    for t in tokens:
        au = str(t.get("actorUuid", ""))
        if au and (au.lower() == ident_l or au.split(".")[-1].lower() == short):
            return str(t.get("id") or ident)
    # 3. Match by token/actor display name.
    for t in tokens:
        if str(t.get("name", "")).lower() == ident_l:
            return str(t.get("id") or ident)
    return ident


async def execute_move_token(
    token_id: str, x: float, y: float, foundry: FoundryClient = None
) -> dict:
    """Move a token on the grid.

    foundry.move_token resolves the identifier (token id / actor uuid / actor id
    / name) inside Foundry, so passing the actor uuid the LLM tends to use now
    works instead of failing 'Entity not found'.
    """
    result = await foundry.move_token(token_id, x, y)
    ok = isinstance(result, dict) and result.get("ok")
    logger.info(f"[Move] {token_id} → ({x}, {y}) {'✓ '+str(result.get('name','')) if ok else '✗ '+str(result)}")
    return {"type": "move_token", "token_id": token_id, "result": result, "success": bool(ok)}


async def _resolve_actor_uuid(identifier: str, foundry: FoundryClient) -> Optional[str]:
    """Map a uuid-or-name to a real actor uuid via the live actor list.

    The LLM frequently invents actor UUIDs (or passes a display name), so a
    direct attribute write fails. Resolve against game.actors by exact uuid,
    then by name, then by the trailing id segment. Returns None if nothing
    matches.
    """
    try:
        actors = await foundry.get_actors()
    except Exception:
        return None
    ident = (identifier or "").strip()
    ident_l = ident.lower()
    short = ident.split(".")[-1]
    for a in actors:
        if a.get("uuid") and a["uuid"] == ident:
            return a["uuid"]
    for a in actors:
        if (a.get("name") or "").lower() == ident_l and ident_l:
            return a.get("uuid")
    for a in actors:
        if short and a.get("uuid", "").split(".")[-1] == short:
            return a.get("uuid")
    return None


async def _apply_hp_once(foundry: FoundryClient, hp_path: str, damage: int, target_uuid: str) -> dict:
    if damage > 0:
        return await foundry.decrease_attribute(hp_path, damage, target_uuid)
    return await foundry.increase_attribute(hp_path, abs(damage), target_uuid)


async def execute_update_hp(
    actor_uuid: str, damage: int, hp_path: str = "hp.value", foundry: FoundryClient = None
) -> dict:
    """Apply damage (positive) or healing (negative) to an actor.

    The *hp_path* parameter comes from the validated Pydantic schema and
    has been sanitized to a simple dotted attribute name (no brackets,
    no arbitrary python expressions).

    If the first write fails — usually because the LLM supplied a hallucinated
    uuid or a display name — the actor is resolved against the live actor list
    and the write is retried once with the canonical uuid.
    """
    target = actor_uuid
    try:
        result = await _apply_hp_once(foundry, hp_path, damage, target)
        failed = isinstance(result, dict) and result.get("success") is False
    except Exception:
        result, failed = None, True

    if failed:
        resolved = await _resolve_actor_uuid(actor_uuid, foundry)
        if not resolved:
            return {
                "type": "update_hp", "actor_uuid": actor_uuid, "damage": damage,
                "success": False,
                "error": (
                    f"No actor matches '{actor_uuid}'. Use an exact uuid from the "
                    "actor list in the context (shown as [uuid: ...])."
                ),
            }
        if resolved == target:
            # The uuid is valid (it's in the live actor list), so the first
            # write failed for a transient reason rather than a bad identifier.
            # An HP change is not idempotent — retrying could double-apply if the
            # original actually landed before the reply was lost — so report the
            # transient failure instead of silently retrying.
            return {
                "type": "update_hp", "actor_uuid": actor_uuid, "damage": damage,
                "success": False,
                "error": f"HP update for actor uuid '{actor_uuid}' failed transiently; not retried.",
            }
        target = resolved
        result = await _apply_hp_once(foundry, hp_path, damage, target)

    if damage > 0:
        logger.info(f"[Damage] {target} took {damage} damage")
    else:
        logger.info(f"[Heal] {target} healed {-damage} HP")
    return {"type": "update_hp", "actor_uuid": target, "damage": damage, "result": result}


# (sound name lower -> src path) built from the world's playlists; cached so a
# multi-sound scene doesn't re-fetch playlists per SFX.
_sound_src_cache: dict = {}
_sound_src_cache_at: float = 0.0


async def _resolve_sound_src(sound_name: str, foundry: FoundryClient) -> Optional[str]:
    """Map a semantic sound name to a real Foundry asset path via the world's
    playlist sounds. Matches exact-name first, then substring, so the LLM's
    approximate names ("low_growl") still find a close sound if one exists."""
    global _sound_src_cache, _sound_src_cache_at
    import time as _t
    import re
    now = _t.monotonic()
    if not _sound_src_cache or now - _sound_src_cache_at > 60:
        catalog: dict = {}
        for pl in await foundry.get_playlists():
            for s in pl.get("sounds", []) or []:
                name = (s.get("name") or "").strip().lower()
                path = s.get("path") or s.get("src")
                if name and path:
                    catalog.setdefault(name, path)
        _sound_src_cache = catalog
        _sound_src_cache_at = now
    want = sound_name.strip().lower()
    if want in _sound_src_cache:
        return _sound_src_cache[want]
    # Fall back to a loose match: the LLM invents names like "creaking_wood"
    # while a playlist sound might be "Creaking Door" — match on any shared word.
    want_words = set(re.split(r"[^a-z0-9]+", want)) - {""}
    for name, path in _sound_src_cache.items():
        if want_words & (set(re.split(r"[^a-z0-9]+", name)) - {""}):
            return path
    return None


async def execute_play_sound(
    sound_name: str, volume: float = 0.5, foundry: FoundryClient = None
) -> dict:
    """Play a sound effect in Foundry.

    The LLM emits a semantic name ("low_growl"); the relay needs a real audio
    `src` path. Resolve the name against the world's playlist sounds and play
    by path. If nothing matches (no SFX library deployed for that sound), skip
    quietly with success — a missing ambient cue must NOT read as a failed
    action, or it triggers a wasted corrective LLM retry every beat.
    """
    src = await _resolve_sound_src(sound_name, foundry)
    if not src:
        logger.info(f"[Sound] no matching sound for '{sound_name}' — skipped")
        return {"type": "play_sound", "sound_name": sound_name, "skipped": True, "success": True}
    result = await foundry.play_sound(src, volume=volume)
    logger.info(f"[Sound] {sound_name} -> {src} (volume {volume})")
    return {"type": "play_sound", "sound_name": sound_name, "src": src, "volume": volume, "result": result}


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


def _known_player_user_ids(app_state) -> set:
    """Foundry user IDs of currently mapped player-owned actors, or empty if unknown."""
    if not app_state or not app_state.state_tracker:
        return set()
    return set(app_state.state_tracker.state.player_actors.values())


async def execute_whisper(
    player_id: str, message: str, foundry: FoundryClient = None, app_state=None
) -> dict:
    """Send a whispered message to a specific player (private message).

    The message is only visible to the specified player_id, not to the whole party.
    Use this for secret information, personal plots, or one-on-one dialogue.

    Foundry accepts any whisper target with no validation — a hallucinated or
    stale player_id (e.g. an actor ID instead of a user ID) creates the message
    but delivers it to no one, with the relay still reporting success. Fall
    back to a public GM message addressed to the named player when player_id
    isn't a known real user ID.
    """
    known_ids = _known_player_user_ids(app_state)
    if known_ids and player_id not in known_ids:
        logger.info(f"[Whisper] '{player_id}' is not a known player user ID; posting publicly")
        addressed = f"**{player_id}** — {message}" if player_id else message
        result = await foundry.chat_message(addressed, speaker="GM")
    else:
        result = await foundry.chat_message(message, speaker="GM", whisper=[player_id])
    logger.info(f"[Whisper] GM → {player_id}: {message[:60]}...")
    return {"type": "whisper", "player_id": player_id, "message": message, "result": result}


async def execute_switch_scene(
    scene_name: str, foundry: FoundryClient = None, app_state=None
) -> dict:
    """Change the current scene."""
    result = await foundry.set_active_scene(scene_name)
    logger.info(f"[Scene] Switched to {scene_name}")
    await _notify_scene_change(app_state, scene_name)
    return {"type": "switch_scene", "scene_name": scene_name, "result": result}


async def execute_start_encounter(
    token_ids: Optional[list] = None,
    encounter_name: Optional[str] = None,
    foundry: FoundryClient = None,
    auto_roll_initiative: bool = True,
    app_state=None,
) -> dict:
    """Begin combat and optionally auto-roll initiative for turn order.

    token_ids is optional — when omitted, all tokens on the active scene are used.
    """
    if not token_ids:
        # Fall back to all tokens currently on the scene
        try:
            scene_tokens = await foundry.get_scene_tokens()
            token_ids = [t["id"] for t in scene_tokens if t.get("id")]
            if not token_ids:
                logger.error(f"[Combat] WARNING: No tokens found on active scene. Scene has {len(scene_tokens)} entries, but none have IDs.")
                for t in scene_tokens:
                    logger.error(f"  Token: {t.get('name', 'Unknown')}, ID: {t.get('id', 'NO_ID')}, disposition: {t.get('disposition')}")
            else:
                logger.info(f"[Combat] Found {len(token_ids)} tokens on scene for combat")
        except Exception as e:
            logger.error(f"[Combat] Could not fetch scene tokens: {e}", exc_info=True)
            token_ids = []

    if not token_ids:
        logger.error("[Combat] ERROR: Cannot start encounter with 0 tokens. Are there tokens on the scene?")
        return {
            "type": "start_encounter",
            "success": False,
            "error": "No tokens found on active scene. Please add tokens to the scene before starting combat.",
            "token_ids": [],
        }

    # Initiative is rolled as part of start-encounter via the relay's rollAll
    # param — there is no separate roll-initiative message type (calling one
    # errored "Unknown message type: roll-initiative" and left combat with no
    # initiative order).
    result = await foundry.start_encounter(
        tokens=token_ids, roll_all=auto_roll_initiative, name=encounter_name
    )
    logger.info(
        f"[Combat] Started encounter{f' {encounter_name!r}' if encounter_name else ''} "
        f"with {len(token_ids)} tokens (roll_initiative={auto_roll_initiative})"
    )

    # Sync state_tracker directly rather than waiting on the relay's
    # combat-event round trip (_handle_combat_event in chat_listener.py).
    # Confirmed live: combat started and rolled initiative fine in Foundry,
    # but /api/status kept reporting mode="exploration" and
    # /api/combat/status kept reporting running=false with an empty
    # turn_order — the AI's own action never told its own state tracker
    # combat had begun, so it stayed stuck narrating as if in exploration.
    if app_state and getattr(app_state, "state_tracker", None):
        await app_state.state_tracker.set_mode("combat")
        await app_state.state_tracker.update_combat(in_combat=True, turn_order=token_ids)

    return {
        "type": "start_encounter", "success": True, "token_ids": token_ids,
        "encounter_name": encounter_name, "result": result,
    }


async def execute_end_encounter(foundry: FoundryClient = None, app_state=None) -> dict:
    """End combat."""
    result = await foundry.end_encounter()
    logger.info("[Combat] Ended encounter")
    if app_state and getattr(app_state, "state_tracker", None):
        await app_state.state_tracker.set_mode("exploration")
        await app_state.state_tracker.update_combat(in_combat=False)
    return {"type": "end_encounter", "result": result}


async def execute_prompt_player(
    player_id: str, question: str, foundry: FoundryClient = None, app_state=None
) -> dict:
    """Ask a specific player for input.

    The *player_id* is meant to be a Foundry user ID, but the LLM often passes
    a display name or an actor ID instead — Foundry accepts any whisper target
    with no validation, so that silently delivers to no one even though the
    relay reports success. Check player_id against the known player_actors
    mapping first and go straight to a public GM message addressed to the
    named player when it isn't recognized; otherwise whisper, with the old
    success-check fallback still covering a genuine relay-level failure.
    """
    known_ids = _known_player_user_ids(app_state)
    if known_ids and player_id not in known_ids:
        logger.info(f"[Prompt] '{player_id}' is not a known player user ID; posting publicly")
        addressed = f"**{player_id}** — {question}" if player_id else question
        result = await foundry.chat_message(addressed, speaker="GM")
    else:
        result = await foundry.chat_message(
            question, speaker=player_id, whisper=[player_id]
        )
        if isinstance(result, dict) and result.get("success") is False:
            logger.info(f"[Prompt] whisper to '{player_id}' failed; posting publicly")
            addressed = f"**{player_id}** — {question}" if player_id else question
            result = await foundry.chat_message(addressed, speaker="GM")
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


_pc_uuid_cache: dict = {}
_pc_uuid_cache_at: float = 0.0


async def _player_actor_name(actor_uuid: str, foundry: FoundryClient) -> Optional[str]:
    """Return the PC's display name if actor_uuid is a player-owned actor, else None.

    Matches on full uuid or its short id suffix (LLM sometimes hands back a bare
    actor id instead of the full "Actor.xxx" uuid). Cached ~30s like
    _is_player_character, to avoid a per-check RPC.
    """
    global _pc_uuid_cache, _pc_uuid_cache_at
    if not actor_uuid or foundry is None:
        return None
    import time as _t
    now = _t.monotonic()
    if not _pc_uuid_cache or now - _pc_uuid_cache_at > 30:
        try:
            actors = await foundry.get_actors(world_only=True)
            _pc_uuid_cache = {
                a.get("uuid", "").lower(): a.get("name", "")
                for a in actors if a.get("has_player_owner") and a.get("uuid")
            }
            _pc_uuid_cache_at = now
        except Exception:
            pass
    key = actor_uuid.strip().lower()
    if key in _pc_uuid_cache:
        return _pc_uuid_cache[key]
    short = key.split(".")[-1]
    for u, name in _pc_uuid_cache.items():
        if u.split(".")[-1] == short:
            return name
    return None


async def execute_skill_check(
    actor_uuid: str, skill: str, dc: int, reason: Optional[str] = None,
    advantage: Optional[bool] = None, foundry: FoundryClient = None
) -> dict:
    """Request a skill check.

    Players roll their own dice — request_skill_check auto-rolls server-side
    (it applies proficiency/expertise/etc. and returns a result directly, no
    player interaction), which silently rolled FOR the player despite the
    docstring's claim otherwise. If actor_uuid is a player-owned character,
    defer to the player instead — same pattern as execute_roll. NPCs/monsters
    still auto-roll via request_skill_check.
    """
    reason_text = f" ({reason})" if reason else ""
    advantage_text = " with advantage" if advantage else (" with disadvantage" if advantage is False else "")

    if getattr(settings, "players_roll_own", True):
        pc_name = await _player_actor_name(actor_uuid, foundry)
        if pc_name:
            await foundry.chat_message(
                f"🎲 **{pc_name}**, make a **{skill.title()}** check (DC {dc})"
                f"{advantage_text}{reason_text}. (Roll from your sheet, or tell me your result.)",
                speaker="GM",
            )
            logger.info(
                f"[Skill Check] Deferred {skill} DC {dc} to player '{pc_name}' "
                f"(players roll their own dice)"
            )
            return {
                "type": "skill_check", "skill": skill, "dc": dc, "advantage": advantage,
                "deferred_to_player": True, "success": True,
            }

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


async def execute_attack_with_item(
    attacker_uuid: str, item_name: str, target_token_id: str,
    foundry: FoundryClient = None
) -> dict:
    """Resolve a real weapon/spell attack: real dnd5e attack + damage rolls
    (attacker's actual ability/proficiency/bonus, midi-qol-aware so any
    Active Effects factor in), hit determined against the target's real AC,
    damage applied via Foundry's own applyDamage (respects temp HP), one
    chat message posted narrating the result.

    Requires the item to have a real dnd5e 5.x attack Activity — items this
    engine deploys for NPCs (campaign/modules/autoanimations.py,
    campaign/modules/midi_qol.py) have one; a bare narrative "roll" action
    is still the right tool for anything without a real Item behind it.
    """
    from foundry import scripts

    resolved_target = await _resolve_token_id(target_token_id, foundry)
    try:
        res = await foundry.execute_js(scripts.resolve_item_attack(attacker_uuid, item_name, resolved_target))
    except Exception as e:
        logger.warning(f"[Attack] {item_name} by {attacker_uuid} failed: {e}")
        return {"type": "attack_with_item", "item": item_name, "success": False, "error": str(e)}

    result = res.get("result") if isinstance(res, dict) else None
    if not (isinstance(result, dict) and result.get("ok")):
        error = (result or {}).get("error", "unknown error") if isinstance(result, dict) else str(res)
        logger.warning(f"[Attack] {item_name} by {attacker_uuid} could not resolve: {error}")
        return {"type": "attack_with_item", "item": item_name, "success": False, "error": error}

    logger.info(
        f"[Attack] {item_name}: {'HIT' if result['hit'] else 'miss'} "
        f"({result['attackTotal']} vs AC {result['targetAc']})"
        + (f", {result['damageTotal']} damage to {result.get('targetName', '?')}" if result["hit"] else "")
    )
    return {
        "type": "attack_with_item", "item": item_name, "target_token_id": resolved_target,
        "success": True, **result,
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

    Analyzes distances, wall cover, and flanking from live scene geometry.
    (The old version ran CombatMechanics over an empty positions dict —
    every field it returned was permanently empty.)
    """
    from combat.tactics import build_tactical_snapshot

    token_id = await _resolve_token_id(actor_uuid, foundry)
    snapshot = await build_tactical_snapshot(foundry, token_id or actor_uuid)

    logger.info(f"[Tactical Analysis] {actor_uuid}: {'analysis rendered' if snapshot else 'nothing tactical on scene'}")

    return {
        "type": "tactical_analysis",
        "actor": actor_uuid,
        "analysis": snapshot or "No enemies visible on the current scene.",
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
    party_level: int, party_size: int, difficulty: str = "medium",
    environment: Optional[str] = None,
    app_state = None, foundry: FoundryClient = None
) -> dict:
    """Generate a balanced encounter from Foundry D&D 5e compendium.

    Uses real monster stat blocks instead of LLM-generated creatures.
    Queries the D&D 5e compendium, selects monsters that fit party power,
    and positions them tactically on the map.
    """
    try:
        from combat.compendium_generator import CompendiumEncounterGenerator

        # Build the generator against the *real* scene so placements land on the
        # canvas and snap to its grid (defaults are only used if the scene query
        # fails).
        scene_w, scene_h, grid = await _resolve_scene_dimensions(foundry)
        gen = CompendiumEncounterGenerator(
            foundry=foundry, scene_width=scene_w, scene_height=scene_h, grid_size=grid
        )
        encounter = await gen.generate(
            party_level=party_level,
            party_size=party_size,
            difficulty=difficulty,
            environment=environment,
        )

        logger.info(
            f"[CompendiumEncounter] {encounter['notes']} "
            f"(adjusted XP {encounter['adjusted_xp']:.0f}/{encounter['budget']:.0f})"
        )

        result = {
            "type": "generate_encounter",
            "encounter": {
                "difficulty": encounter["difficulty_rating"],
                "notes": encounter["notes"],
                "budget": encounter["budget"],
                "adjusted_xp": encounter["adjusted_xp"],
                "creatures": encounter["creatures"],
                "placements": encounter["placements"],
            }
        }

        # Deploy to Foundry if connected.
        if foundry and foundry.is_connected:
            from campaign.monster_actor import ensure_monster_actor
            placed_tokens = []

            for placement in encounter["placements"]:
                monster_name = placement.get("name", "Monster")
                cr = placement.get("cr", 1)
                x = placement.get("x", 200)
                y = placement.get("y", 200)

                if placement.get("source") == "world":
                    # Existing campaign NPC — already a world actor; place by its
                    # own UUID, no import needed.
                    world_uuid = placement.get("uuid", "")
                else:
                    # Compendium monster — import the real stat block into the
                    # world (or reuse an existing world actor), then place by the
                    # resolved world UUID. Placing by name alone fails for
                    # monsters not yet in the world.
                    world_uuid = await ensure_monster_actor(foundry, monster_name, cr=cr)
                if not world_uuid:
                    logger.warning(
                        f"[CompendiumEncounter] Could not resolve actor for "
                        f"'{monster_name}' — skipping"
                    )
                    continue

                token_result = await foundry.place_token(
                    uuid=world_uuid, x=x, y=y, disposition=-1
                )
                if token_result and "error" not in token_result:
                    tid = _extract_token_id(token_result)
                    if tid:
                        placed_tokens.append(tid)
                        logger.debug(
                            f"[CompendiumEncounter] Placed {monster_name} at ({x}, {y})"
                        )
                    else:
                        logger.warning(
                            f"[CompendiumEncounter] Placed {monster_name} but could not "
                            f"read a token id from result keys={list(token_result)}"
                        )

            if placed_tokens:
                # Start combat with EVERY token on the scene (party included),
                # not just the placed monsters — otherwise the combat tracker
                # has hostiles only and the PCs never get initiative.
                combat_ids = list(placed_tokens)
                try:
                    scene_tokens = await foundry.get_scene_tokens()
                    combat_ids = [t["id"] for t in scene_tokens if t.get("id")] or combat_ids
                except Exception as e:
                    logger.warning(
                        f"[CompendiumEncounter] Could not fetch scene tokens for "
                        f"combat ({e}) — starting with placed monsters only"
                    )
                await foundry.start_encounter(combat_ids, roll_all=True)
                logger.info(
                    f"[CompendiumEncounter] Deployed {len(placed_tokens)} tokens, "
                    f"started encounter with {len(combat_ids)} combatants"
                )

            result["placed_tokens"] = placed_tokens
            result["deployed_to_foundry"] = len(placed_tokens) > 0

        return result

    except Exception as e:
        logger.error(f"[CompendiumEncounter] Generation failed: {e}", exc_info=True)
        return {"type": "generate_encounter", "error": str(e)}


def _extract_token_id(res: dict) -> str:
    """Pull the created/moved token id from place_token's varied return shapes.

    - move/dedup path: {"moved": True, "token_id": "..."}
    - create path (canvas_create): {"data": [{"_id": "..."}], "type": "create-canvas-document-result"}
    - simple: {"id": "..."}
    """
    if not isinstance(res, dict):
        return ""
    tid = res.get("token_id") or res.get("id")
    if tid:
        return tid
    data = res.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0].get("_id") or data[0].get("id") or ""
    if isinstance(data, dict):
        return data.get("_id") or data.get("id") or ""
    return ""


async def _resolve_scene_dimensions(foundry: FoundryClient) -> tuple:
    """Return (width, height, grid_size) for the active scene, with safe defaults.

    Foundry scene payloads vary in shape across versions (top-level vs. nested
    under "data"; grid as a number or a {size} object), so parse defensively.
    """
    width, height, grid = 800, 600, 100
    try:
        if not foundry:
            return width, height, grid
        details = await foundry.get_scene_details()
        if not isinstance(details, dict):
            return width, height, grid
        data = details.get("data") if isinstance(details.get("data"), dict) else {}
        width = int(details.get("width") or data.get("width") or width)
        height = int(details.get("height") or data.get("height") or height)
        g = details.get("grid", data.get("grid"))
        if isinstance(g, dict):
            grid = int(g.get("size") or grid)
        elif isinstance(g, (int, float)) and g:
            grid = int(g)
        else:
            grid = int(details.get("gridSize") or data.get("gridSize") or grid)
    except Exception as e:
        logger.debug(f"[CompendiumEncounter] Scene dimension lookup failed: {e}")
    return width, height, max(1, grid)


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
        logger.error(f"[Procedural] Treasure generation failed: {e}", exc_info=True)
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

            # Offset by the current token count so multiple generated NPCs
            # don't stack on the same square.
            try:
                n_existing = len(await foundry.get_scene_tokens())
            except Exception:
                n_existing = 0
            token_result = await foundry.place_token(name, x=400 + (n_existing % 8) * 100, y=400, disposition=0)
            token_id = (token_result or {}).get("id", "") if "error" not in (token_result or {}) else ""

            result["npc"]["actor_uuid"] = actor_uuid
            result["npc"]["token_id"] = token_id
            result["deployed_to_foundry"] = bool(actor_uuid)
            logger.info(f"[Procedural] Created NPC actor {actor_uuid}, token {token_id}")

        return result
    except Exception as e:
        logger.error(f"[Procedural] NPC generation failed: {e}", exc_info=True)
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
        logger.error(f"[Procedural] Quest generation failed: {e}", exc_info=True)
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
    actor_name: Optional[str] = None, x: float = 0.0, y: float = 0.0,
    disposition: int = 0, hidden: bool = False,
    uuid: Optional[str] = None,
    foundry: FoundryClient = None
) -> dict:
    """Place an actor's token on the current scene (by name or uuid)."""
    result = await foundry.place_token(
        actor_name, x, y, disposition=disposition, hidden=hidden, uuid=uuid
    )
    logger.info(f"[Token] Placed '{actor_name}' at ({x}, {y}) disposition={disposition}")

    # ── Extract and track scene from token placement ──────────────────────
    # When a token is placed, Foundry returns the sceneId. Track this so
    # subsequent operations (get_scene_tokens, start_encounter, etc.) know
    # which scene to operate on.
    scene_id = result.get("sceneId") if isinstance(result, dict) else None
    if scene_id and hasattr(foundry, "_track_scene"):
        try:
            foundry._track_scene(scene_id)
            logger.debug(f"[Token] Tracked scene {scene_id} from token placement")
        except Exception as e:
            logger.debug(f"[Token] Could not track scene: {e}")

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
    # Default to False (Levels module handles vision) but honour an explicit caller value
    # so instances without Levels can enable token vision when needed.
    updates["tokenVision"] = tokenVision if tokenVision is not None else False
    if grid_size is not None:
        updates["grid"] = {"size": grid_size}

    if not updates:
        return {"type": "configure_scene", "result": "no changes"}

    result = await foundry.configure_scene(updates, scene_name=scene_name)
    logger.info(f"[Scene] Configured: {list(updates.keys())}")
    return {"type": "configure_scene", "updates": updates, "result": result}


async def execute_setup_scene(
    scene_name: Optional[str] = None,
    background_src: Optional[str] = None,
    walls: Optional[list] = None,
    lights: Optional[list] = None,
    sounds: Optional[list] = None,
    tokens: Optional[list] = None,
    darkness: Optional[float] = None,
    grid_size: Optional[int] = None,
    fog_exploration: Optional[bool] = None,
    global_illumination: Optional[bool] = None,
    tokenVision: Optional[bool] = None,
    clear_walls: bool = False,
    clear_lights: bool = False,
    clear_tokens: bool = False,
    narrate: Optional[str] = None,
    foundry: FoundryClient = None,
    app_state=None,
) -> dict:
    """Full scene setup — walls, lights, sounds, tokens, and scene config in sequence."""
    results = {}

    # Switch to named scene first if provided
    if scene_name:
        try:
            await foundry.set_active_scene(scene_name)
            results["scene_switch"] = "ok"
            logger.info(f"[Setup] Switched to scene: {scene_name}")
            # Wait for canvas to be ready instead of sleeping. canvasReady is the
            # only scene-activation hook the relay's REST API module actually
            # forwards (see FORWARDED_HOOKS in its eventChannels.ts); fall back
            # to a short sleep if it doesn't fire for some other reason.
            hook_fired = await foundry.wait_for_hook("canvasReady", timeout=10)
            if not hook_fired:
                await asyncio.sleep(1)  # fallback if the hook doesn't fire
        except Exception as e:
            logger.warning(f"[Setup] Could not switch to scene '{scene_name}': {e}")

    # Set background image if provided
    if background_src:
        try:
            await foundry.configure_scene({"background": {"src": background_src}})
            results["background"] = background_src
            logger.info(f"[Setup] Set background: {background_src}")
        except Exception as e:
            logger.warning(f"[Setup] Background set failed: {e}")

    # Configure scene-level settings
    scene_updates = {}
    if darkness is not None:
        scene_updates["darkness"] = darkness
    if global_illumination is not None:
        scene_updates["globalLight"] = global_illumination
    if fog_exploration is not None:
        scene_updates["fogExploration"] = fog_exploration
    # Default to False (Levels module handles vision) but honour an explicit caller value.
    scene_updates["tokenVision"] = tokenVision if tokenVision is not None else False
    if grid_size is not None:
        scene_updates["grid"] = {"size": grid_size}
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
        try:
            # Honor the caller's clear_tokens flag (default False). place_token
            # dedupes by actor, so skipping the clear won't create duplicates —
            # and it preserves PC tokens already on the scene (e.g. auto-placed
            # by set_active_scene), which an unconditional clear would wipe.
            if clear_tokens:
                await foundry.clear_canvas_layer("tokens")
                logger.info("[Setup] Cleared existing tokens before placement")
            for tok in tokens:
                actor_name = tok.get("actor_name") or tok.get("name")
                x = tok.get("x", 0)
                y = tok.get("y", 0)
                disposition = tok.get("disposition", 0)
                hidden = tok.get("hidden", False)
                if actor_name:
                    try:
                        tok_result = await foundry.place_token(actor_name, x, y, disposition=disposition, hidden=hidden)
                        if isinstance(tok_result, dict) and tok_result.get("error"):
                            logger.warning(f"[Setup] Failed to place token '{actor_name}': {tok_result['error']}")
                        else:
                            placed += 1
                    except Exception as e:
                        logger.warning(f"[Setup] Failed to place token '{actor_name}': {e}")
            results["tokens"] = placed
            logger.info(f"[Setup] Placed {placed}/{len(tokens)} tokens")
        except Exception as e:
            logger.warning(f"[Setup] Token placement failed: {e}")
            results["tokens_error"] = str(e)

    # Narrate after setup — goes through the full narrate executor so TTS fires
    if narrate:
        try:
            await execute_narrate(narrate, foundry)
            results["narrated"] = True
        except Exception as e:
            logger.warning(f"[Setup] Narration failed: {e}")

    # Refresh scene awareness now that the switch + token placement are done, so
    # the cache/familiarity/encounter-context reflect the final scene state.
    if scene_name:
        await _notify_scene_change(app_state, scene_name)

    logger.info(f"[Setup] Scene setup complete: {results}")
    return {"type": "setup_scene", "results": results, "success": True}


async def execute_generate_map(
    prompt: str,
    scene_name: str,
    style: str = "dungeon",
    size: str = "medium",
    switch_to_scene: bool = True,
    narration: Optional[str] = None,
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
        logger.error(f"[MapGen] ComfyUI generation failed: {e}", exc_info=True)
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

        # Narrate the new location so players know where they are
        if narration and foundry:
            try:
                await execute_narrate(narration, foundry)
            except Exception as _ne:
                logger.warning(f"[MapGen] Narration failed: {_ne}")

        return {
            "type": "generate_map",
            "scene_name": scene_name,
            "background": background_src,
            "dimensions": {"width": width, "height": height},
            "success": True,
        }
    except Exception as e:
        logger.error(f"[MapGen] Scene creation failed: {e}", exc_info=True)
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
    desc = description or (code[:60] if code else "<empty>")
    if not code or not code.strip():
        logger.warning("[JS] execute_js called with empty code")
        return {"type": "execute_js", "description": desc, "success": False, "error": "Code is empty"}
    if not getattr(_settings, "allow_execute_js", False):
        logger.warning(f"[JS] Blocked execute_js (allow_execute_js=false): {desc}")
        return {
            "type": "execute_js",
            "description": desc,
            "success": False,
            "error": "execute_js is disabled. Set ALLOW_EXECUTE_JS=true to enable arbitrary Foundry JavaScript.",
        }
    logger.info(f"[JS] Executing: {desc}")
    if not foundry or not foundry.is_connected:
        logger.error("[JS] execute_js called with disconnected Foundry client")
        return {"type": "execute_js", "description": desc, "success": False, "error": "Foundry is not connected"}
    result = await foundry.execute_js(code)
    return {"type": "execute_js", "description": desc, "result": result}


async def execute_pause_game(
    reason: Optional[str] = None,
    foundry: FoundryClient = None,
    app_state=None,
) -> dict:
    """Pause both the AI-GM and FoundryVTT."""
    # Pause AI processing
    chat_listener = getattr(app_state, "chat_listener", None)
    if chat_listener:
        chat_listener._running = False

    # Pause Foundry for all players. This uses a FIXED, non-parameterized JS
    # snippet and is deliberately exempt from the allow_execute_js gate (which
    # only blocks the LLM-driven, arbitrary execute_js action): pausing is core
    # control that must work even when arbitrary JS is disabled, and there is no
    # injection surface here.
    if foundry:
        try:
            await foundry.execute_js("if(!game.paused){game.togglePause(true,true);}")
        except Exception as e:
            logger.warning(f"[Pause] Foundry pause failed: {e}")

    if reason:
        try:
            await foundry.chat_message(f"*{reason}*", speaker="GM")
        except Exception:
            pass

    logger.info(f"[Pause] Game paused. reason={reason!r}")
    return {"type": "pause_game", "reason": reason}


async def execute_resume_game(
    foundry: FoundryClient = None,
    app_state=None,
) -> dict:
    """Resume both the AI-GM and FoundryVTT."""
    # Resume AI processing
    chat_listener = getattr(app_state, "chat_listener", None)
    if chat_listener:
        chat_listener._running = True

    # Unpause Foundry for all players. Fixed JS snippet, intentionally exempt
    # from the allow_execute_js gate — see execute_pause_game for rationale.
    if foundry:
        try:
            await foundry.execute_js("if(game.paused){game.togglePause(false,true);}")
        except Exception as e:
            logger.warning(f"[Resume] Foundry unpause failed: {e}")

    logger.info("[Resume] Game resumed.")
    return {"type": "resume_game"}


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
    "attack_with_item": execute_attack_with_item,
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
    "pause_game": execute_pause_game,
    "resume_game": execute_resume_game,
}
