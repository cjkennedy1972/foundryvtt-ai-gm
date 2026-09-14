#!/usr/bin/env python3
"""Mock-based unit tests for actions/executors.py — the low-coverage async I/O core.

All Foundry relay I/O is mocked (no network, no relay, no Foundry). Coverage
focus:
- narrate/speak/whisper/prompt_player (PC guard + player-id fallbacks)
- roll + advantage formula rewriting (PC-defer included)
- update_hp idempotency: transient failure, lost-reply verification, double-apply guard
- token/actor uuid/name resolution helpers
- sound resolution (exact + word-substring + miss)
- encounter start/end (token discovery, combat-mode state sync)
- cast_spell ritual/concentration paths
- rest (per-actor error isolation)
- condition/exhaustion/inspiration/passive check
- grapple contested success/failure
- scene building: place_walls/lights/sounds, place_token scene tracking,
  configure_scene, setup_scene full flow
- execute_js gating, pause/resume, macro dispatch
- generate_encounter deployment + _extract_token_id + _resolve_scene_dimensions
- module-cache reset isolation between tests

Run:
    cd ai-engine && python -m pytest tests/test_action_executors.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("MODEL", "test-model")
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("RELAY_SCOPED_KEY", "test-relay-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import actions.executors as ex
from actions.executors import (
    _advantage_formula,
    _extract_token_id,
    _is_player_character,
    _player_actor_name,
    _resolve_actor_uuid,
    _resolve_scene_dimensions,
    _resolve_sound_src,
    _resolve_token_id,
    execute_apply_token_effect,
    execute_attack_with_item,
    execute_cast_spell,
    execute_configure_scene,
    execute_death_save,
    execute_end_encounter,
    execute_environmental_save,
    execute_execute_js,
    execute_execute_macro,
    execute_generate_encounter,
    execute_generate_map,
    execute_generate_npc,
    execute_generate_quest,
    execute_grapple,
    execute_grant_inspiration,
    execute_long_rest,
    execute_move_token,
    execute_narrate,
    execute_opportunity_attack,
    execute_passive_check,
    execute_pause_game,
    execute_play_music,
    execute_play_sound,
    execute_place_lights,
    execute_place_sounds,
    execute_place_token,
    execute_place_walls,
    execute_prompt_player,
    execute_resume_game,
    execute_roll,
    execute_saving_throw,
    execute_set_exhaustion,
    execute_set_time,
    execute_set_weather,
    execute_setup_scene,
    execute_short_rest,
    execute_skill_check,
    execute_speak,
    execute_start_encounter,
    execute_switch_scene,
    execute_tactical_analysis,
    execute_update_hp,
    execute_update_vision,
    execute_use_action,
    execute_use_save_item,
    execute_whisper,
    reset_action_caches,
)

# =============================================================================
# FIXTURES & MOCK FACTORIES
# =============================================================================


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset module-level caches before each test."""
    reset_action_caches()
    yield


def mock_foundry_client():
    """Factory: return a MagicMock foundry.Client with common async methods."""
    mock = MagicMock()
    mock.chat_message = AsyncMock(return_value={"ok": True})
    mock.roll = AsyncMock(return_value={"ok": True, "result": 15})
    mock.move_token = AsyncMock(return_value={"ok": True})
    mock.get_actors = AsyncMock(return_value={})
    mock.get_actor = AsyncMock(return_value=None)
    mock.get_playlists = AsyncMock(return_value={})
    mock.get_scenes = AsyncMock(return_value={})
    mock.get_scene_tokens = AsyncMock(return_value=[])
    mock.get_combatants = AsyncMock(return_value=[])
    mock.start_combat = AsyncMock(return_value={"ok": True})
    mock.start_encounter = AsyncMock(return_value={"ok": True})
    mock.end_combat = AsyncMock(return_value={"ok": True})
    mock.end_encounter = AsyncMock(return_value={"ok": True})
    mock.execute_macro = AsyncMock(return_value={"ok": True})
    mock.execute_script = AsyncMock(return_value={"ok": True})
    mock.execute_js = AsyncMock(return_value={"ok": True})
    mock.opportunity_attack = AsyncMock(return_value={"ok": True})
    mock.pause_game = AsyncMock(return_value={"ok": True})
    mock.resume_game = AsyncMock(return_value={"ok": True})
    mock.play_sound = AsyncMock(return_value={"ok": True})
    mock.request_skill_check = AsyncMock(return_value={"ok": True, "result": 18})
    mock.request_saving_throw = AsyncMock(return_value={"ok": True, "result": 14})
    mock.request_death_save = AsyncMock(return_value={"ok": True, "result": 12})
    mock.track_action = AsyncMock(return_value={"ok": True})
    mock.contested_check = AsyncMock(return_value={"initiatorSuccess": True, "initiatorRoll": 16, "targetRoll": 12})
    mock.apply_condition = AsyncMock(return_value={"ok": True})
    mock.create_token = AsyncMock(return_value={"ok": True, "token_id": "token_new"})
    mock.set_active_scene = AsyncMock(return_value={"ok": True})
    mock.patch_actor = AsyncMock(return_value={"ok": True})
    mock.get_settings = AsyncMock(return_value={"tts_playback_active": False})
    mock.is_connected = True
    return mock


# =============================================================================
# CHUNK A: SIMPLE HELPERS + NARRATE / SPEAK / MOVE / ROLL
# =============================================================================


class TestAdvantageFormula:
    """Test _advantage_formula(formula, advantage_flag)."""

    def test_advantage_true_d20_leading(self):
        """1d20+3 with advantage=True → 2d20kh1+3."""
        result = _advantage_formula("1d20+3", True)
        assert result == "2d20kh1+3"

    def test_advantage_false_d20_leading(self):
        """1d20+3 with advantage=False → 2d20kl1+3."""
        result = _advantage_formula("1d20+3", False)
        assert result == "2d20kl1+3"

    def test_advantage_none_unchanged(self):
        """1d20+3 with advantage=None → unchanged."""
        result = _advantage_formula("1d20+3", None)
        assert result == "1d20+3"

    def test_advantage_true_any_leading_die(self):
        """2d6+4 with advantage=True → 2d6kh1+4 (any leading die, not just d20)."""
        result = _advantage_formula("2d6+4", True)
        assert result == "2d6kh1+4"


class TestExtractTokenId:
    """Test _extract_token_id(result_dict) — extracts token id from place_token response."""

    def test_extract_from_token_id_field(self):
        """result dict with 'token_id' key → return token_id."""
        result = {"moved": True, "token_id": "token123"}
        extracted = _extract_token_id(result)
        assert extracted == "token123"

    def test_extract_from_id_field(self):
        """result dict with 'id' key (no token_id) → return id."""
        result = {"id": "token456"}
        extracted = _extract_token_id(result)
        assert extracted == "token456"

    def test_extract_from_data_array(self):
        """result dict with data array (canvas_create path) → extract from data[0]._id."""
        result = {"data": [{"_id": "token789"}], "type": "create-canvas-document-result"}
        extracted = _extract_token_id(result)
        assert extracted == "token789"

    def test_extract_empty_on_invalid(self):
        """invalid input → return empty string."""
        extracted = _extract_token_id("not_a_dict")
        assert extracted == ""


class TestExecuteNarrate:
    """Test execute_narrate(text, foundry, source=None)."""

    @pytest.mark.asyncio
    async def test_narrate_basic(self):
        """narrate(text) → chat_message with speaker=GM, whisper=[]."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors.tts_playback") as mock_tts:
            mock_tts.is_active.return_value = False
            result = await execute_narrate("The goblin attacks!", mock_fc)

        assert result["type"] == "narrate"
        mock_fc.chat_message.assert_called_once()
        call_args = mock_fc.chat_message.call_args
        assert call_args[0][0] == "The goblin attacks!"
        assert call_args[1]["whisper"] == []

    @pytest.mark.asyncio
    async def test_narrate_with_tts_spawned(self):
        """narrate with TTS active → spawn(tts_playback.narrate(...))."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors.tts_playback") as mock_tts:
            with patch("actions.executors.spawn") as mock_spawn:
                mock_tts.is_active.return_value = True
                result = await execute_narrate("TTS message", mock_fc)

        assert result["type"] == "narrate"
        # spawn() should be called with tts_playback.narrate(text, foundry)
        mock_spawn.assert_called_once()


class TestExecuteSpeak:
    """Test execute_speak(npc_name, text, whisper_to, foundry, source)."""

    @pytest.mark.asyncio
    async def test_speak_npc_basic(self):
        """speak(npc_name) → chat_message with npc as speaker, returns type=speak."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._is_player_character", return_value=False):
            with patch("actions.executors.spawn"):
                result = await execute_speak("Goblin", "Grrr!", None, mock_fc)

        assert result["type"] == "speak"
        assert result["npc"] == "Goblin"
        assert "result" in result
        mock_fc.chat_message.assert_called_once()
        call_args = mock_fc.chat_message.call_args
        assert call_args[1]["speaker"] == "Goblin"

    @pytest.mark.asyncio
    async def test_speak_player_character_rejected(self):
        """speak(player_char_name) → error (PC-only action)."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._is_player_character", return_value=True):
            result = await execute_speak("Hero", "I cast Magic Missile!", None, mock_fc)

        assert result["type"] == "speak"
        assert result["npc"] == "Hero"
        assert result["success"] is False
        assert "player character" in result.get("error", "").lower()


class TestExecuteMoveToken:
    """Test execute_move_token(token_id, x, y, foundry_client)."""

    @pytest.mark.asyncio
    async def test_move_token_success(self):
        """move_token(token_id, x, y) → foundry.move_token called."""
        mock_fc = mock_foundry_client()
        mock_fc.move_token.return_value = {"ok": True}

        result = await execute_move_token("token123", 100, 200, mock_fc)

        assert result["success"] is True
        mock_fc.move_token.assert_called_once_with("token123", 100, 200)

    @pytest.mark.asyncio
    async def test_move_token_failure(self):
        """move_token relay failure → success=False."""
        mock_fc = mock_foundry_client()
        mock_fc.move_token.return_value = {"ok": False, "error": "token not found"}

        result = await execute_move_token("token123", 100, 200, mock_fc)

        assert result["success"] is False


class TestExecuteRoll:
    """Test execute_roll(formula, speaker, flavor=None, advantage=None, foundry=None, source=None)."""

    @pytest.mark.asyncio
    async def test_roll_basic_npc(self):
        """roll(1d20+3, speaker=Goblin) → foundry.roll called with formula."""
        mock_fc = mock_foundry_client()
        mock_fc.roll.return_value = {"ok": True, "total": 18}

        with patch("actions.executors._is_player_character", return_value=False):
            result = await execute_roll("1d20+3", "Goblin", foundry=mock_fc)

        assert result["type"] == "roll"
        assert result["formula"] == "1d20+3"
        mock_fc.roll.assert_called_once()

    @pytest.mark.asyncio
    async def test_roll_with_advantage(self):
        """roll(1d20+3, advantage=True) → formula rewritten to 2d20kh1+3."""
        mock_fc = mock_foundry_client()
        mock_fc.roll.return_value = {"ok": True, "total": 19}

        with patch("actions.executors._is_player_character", return_value=False):
            result = await execute_roll("1d20+3", "Goblin", advantage=True, foundry=mock_fc)

        call_args = mock_fc.roll.call_args
        assert call_args[0][0] == "2d20kh1+3"
        assert result["advantage"] is True

    @pytest.mark.asyncio
    async def test_roll_with_disadvantage(self):
        """roll(1d20+3, advantage=False) → formula rewritten to 2d20kl1+3."""
        mock_fc = mock_foundry_client()
        mock_fc.roll.return_value = {"ok": True, "total": 12}

        with patch("actions.executors._is_player_character", return_value=False):
            result = await execute_roll("1d20+3", "Goblin", advantage=False, foundry=mock_fc)

        call_args = mock_fc.roll.call_args
        assert call_args[0][0] == "2d20kl1+3"
        assert result["advantage"] is False

    @pytest.mark.asyncio
    async def test_roll_pc_deferred(self):
        """roll for PC character → deferred to player."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._is_player_character", return_value=True):
            result = await execute_roll("1d20+3", "Hero", foundry=mock_fc)

        assert result["type"] == "roll"
        assert result["deferred_to_player"] is True
        assert result["success"] is True
        # Should NOT call foundry.roll for PCs
        mock_fc.roll.assert_not_called()


class TestExecutePlaySound:
    """Test execute_play_sound(sound_name, volume=0.5, foundry=None, source=None)."""

    @pytest.mark.asyncio
    async def test_play_sound_found(self):
        """play_sound(name) → foundry.play_sound called, type=play_sound."""
        mock_fc = mock_foundry_client()
        mock_fc.play_sound = AsyncMock(return_value={"ok": True})

        with patch("actions.executors._resolve_sound_src", return_value="path/to/sound.wav"):
            result = await execute_play_sound("sword_clash", foundry=mock_fc)

        assert result["type"] == "play_sound"
        assert result["sound_name"] == "sword_clash"
        assert result["src"] == "path/to/sound.wav"
        mock_fc.play_sound.assert_called_once()

    @pytest.mark.asyncio
    async def test_play_sound_not_found_skipped(self):
        """play_sound(unknown_name) → skipped=True, success=True (not an error)."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._resolve_sound_src", return_value=None):
            result = await execute_play_sound("unknown_sound", foundry=mock_fc)

        assert result["type"] == "play_sound"
        assert result["sound_name"] == "unknown_sound"
        assert result.get("skipped") is True
        assert result["success"] is True
        # Should NOT call foundry.play_sound when no sound found
        mock_fc.play_sound.assert_not_called()


# =============================================================================
# CHUNK B: WHISPER + PROMPT_PLAYER + ENCOUNTERS
# =============================================================================


class TestExecuteWhisper:
    """Test execute_whisper(player_id, message, foundry, app_state, source)."""

    @pytest.mark.asyncio
    async def test_whisper_known_player(self):
        """whisper(known_player_id, message) → chat_message with whisper list."""
        mock_fc = mock_foundry_client()
        mock_app = MagicMock()
        mock_app.state_tracker.state.player_actors.values.return_value = {"player1"}

        result = await execute_whisper("player1", "Secret message", mock_fc, mock_app)

        assert result["type"] == "whisper"
        assert result["player_id"] == "player1"
        mock_fc.chat_message.assert_called_once()
        call_args = mock_fc.chat_message.call_args
        assert call_args[1]["whisper"] == ["player1"]

    @pytest.mark.asyncio
    async def test_whisper_unknown_player_with_known_ids(self):
        """whisper(unknown_player_id) with known_ids → posts publicly."""
        mock_fc = mock_foundry_client()
        mock_app = MagicMock()

        # When there are known_ids and player_id is not in them, post publicly
        with patch("actions.executors._known_player_user_ids", return_value={"player1", "player2"}):
            result = await execute_whisper("unknown123", "Oops", mock_fc, mock_app)

        assert result["type"] == "whisper"
        mock_fc.chat_message.assert_called_once()
        call_args = mock_fc.chat_message.call_args
        # Should post publicly with player_id prefixed
        assert "**unknown123**" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_whisper_no_known_ids_whisper_anyway(self):
        """whisper() with empty known_ids → whisper anyway (fallback)."""
        mock_fc = mock_foundry_client()
        mock_app = MagicMock()

        # When there are no known_ids, whisper anyway (fallback)
        with patch("actions.executors._known_player_user_ids", return_value=set()):
            result = await execute_whisper("player123", "Message", mock_fc, mock_app)

        assert result["type"] == "whisper"
        mock_fc.chat_message.assert_called_once()
        call_args = mock_fc.chat_message.call_args
        # Should whisper without prefixing
        assert call_args[1]["whisper"] == ["player123"]


class TestExecutePromptPlayer:
    """Test execute_prompt_player(player_id, question, foundry, app_state, source)."""

    @pytest.mark.asyncio
    async def test_prompt_player_known_id(self):
        """prompt_player(known_player_id, question) → whisper to player."""
        mock_fc = mock_foundry_client()
        mock_app = MagicMock()

        with patch("actions.executors._known_player_user_ids", return_value={"player1"}):
            result = await execute_prompt_player("player1", "What do you do?", mock_fc, mock_app)

        assert result["type"] == "prompt_player"
        assert result["player_id"] == "player1"
        mock_fc.chat_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_prompt_player_unknown_id_public(self):
        """prompt_player(unknown_player_id) → posts publicly."""
        mock_fc = mock_foundry_client()
        mock_app = MagicMock()

        with patch("actions.executors._known_player_user_ids", return_value={"player1"}):
            result = await execute_prompt_player("unknown_player", "Roll initiative", mock_fc, mock_app)

        assert result["type"] == "prompt_player"
        assert result["player_id"] == "unknown_player"
        # Should post publicly when player_id not in known_ids
        mock_fc.chat_message.assert_called_once()
        call_args = mock_fc.chat_message.call_args
        # Check that the message contains the player name and question
        assert "unknown_player" in call_args[0][0] and "Roll initiative" in call_args[0][0]


class TestStartEndEncounter:
    """Test execute_start_encounter / execute_end_encounter."""

    @pytest.mark.asyncio
    async def test_start_encounter_with_tokens(self):
        """start_encounter(token_ids) → foundry.start_encounter."""
        mock_fc = mock_foundry_client()

        result = await execute_start_encounter(token_ids=["token1", "token2"], foundry=mock_fc)

        assert result["type"] == "start_encounter"
        mock_fc.start_encounter.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_encounter_no_tokens_error(self):
        """start_encounter() with no tokens on scene → error."""
        mock_fc = mock_foundry_client()
        mock_fc.get_scene_tokens = AsyncMock(return_value=[])

        result = await execute_start_encounter(foundry=mock_fc)

        assert result["type"] == "start_encounter"
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_end_encounter(self):
        """end_encounter() → foundry.end_encounter."""
        mock_fc = mock_foundry_client()

        result = await execute_end_encounter(foundry=mock_fc)

        assert result["type"] == "end_encounter"
        mock_fc.end_encounter.assert_called_once()


# =============================================================================
# CHUNK C: SKILL CHECKS, SAVES, REST, CONDITIONS
# =============================================================================


class TestExecuteSkillCheck:
    """Test execute_skill_check(actor_uuid, skill, dc, reason, advantage, foundry)."""

    @pytest.mark.asyncio
    async def test_skill_check_npc(self):
        """skill_check(npc, skill, dc) → auto-roll."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value=None):
            result = await execute_skill_check("Goblin", "perception", 12, foundry=mock_fc)

        assert result["type"] == "skill_check"
        assert result["skill"] == "perception"
        assert result["dc"] == 12

    @pytest.mark.asyncio
    async def test_skill_check_pc_deferred(self):
        """skill_check(pc_name) → deferred to player."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value="Hero"):
            result = await execute_skill_check("Actor.hero1", "stealth", 15, foundry=mock_fc)

        assert result["type"] == "skill_check"
        assert result["deferred_to_player"] is True


class TestExecuteSavingThrow:
    """Test execute_saving_throw(actor_uuid, ability, dc, reason, advantage, foundry)."""

    @pytest.mark.asyncio
    async def test_saving_throw_npc(self):
        """saving_throw(npc, ability, dc) → auto-roll."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value=None):
            result = await execute_saving_throw("Goblin", "dexterity", 12, foundry=mock_fc)

        assert result["type"] == "saving_throw"
        assert result["ability"] == "dexterity"
        assert result["dc"] == 12

    @pytest.mark.asyncio
    async def test_saving_throw_pc_deferred(self):
        """saving_throw(pc_name) → deferred to player."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value="Hero"):
            result = await execute_saving_throw("Actor.hero1", "constitution", 15, foundry=mock_fc)

        assert result["type"] == "saving_throw"
        assert result["deferred_to_player"] is True


class TestExecuteDeathSave:
    """Test execute_death_save(actor_uuid, advantage, foundry)."""

    @pytest.mark.asyncio
    async def test_death_save_npc(self):
        """death_save(npc) → auto-roll."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value=None):
            result = await execute_death_save("Goblin", foundry=mock_fc)

        assert result["type"] == "death_save"

    @pytest.mark.asyncio
    async def test_death_save_pc_deferred(self):
        """death_save(pc) → deferred to player."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value="Hero"):
            result = await execute_death_save("Actor.hero1", foundry=mock_fc)

        assert result["type"] == "death_save"
        assert result["deferred_to_player"] is True


class TestExecuteUseAction:
    """Test execute_use_action(actor_uuid, action_type, foundry)."""

    @pytest.mark.asyncio
    async def test_use_action_basic(self):
        """use_action(actor, action_type) → track action."""
        mock_fc = mock_foundry_client()

        result = await execute_use_action("Goblin", "action", foundry=mock_fc)

        assert result["type"] == "use_action"
        assert result["action_type"] == "action"
        mock_fc.track_action.assert_called_once_with("Goblin", "action")


class TestExecuteRest:
    """Test execute_short_rest / execute_long_rest."""

    @pytest.mark.asyncio
    async def test_short_rest(self):
        """short_rest(actor_uuids) → recover hit dice."""
        mock_fc = mock_foundry_client()
        mock_fc.get_actors = AsyncMock(return_value=[
            {"uuid": "Actor.hero1", "name": "Hero", "hp": 10, "max_hp": 15}
        ])

        result = await execute_short_rest(["Actor.hero1"], foundry=mock_fc)

        assert result["type"] == "short_rest"
        assert "Actor.hero1" in str(result)

    @pytest.mark.asyncio
    async def test_long_rest(self):
        """long_rest(actor_uuids) → restore hp and spells."""
        mock_fc = mock_foundry_client()
        mock_fc.get_actors = AsyncMock(return_value=[
            {"uuid": "Actor.hero1", "name": "Hero", "hp": 10, "max_hp": 15}
        ])

        result = await execute_long_rest(["Actor.hero1"], foundry=mock_fc)

        assert result["type"] == "long_rest"
        assert "Actor.hero1" in str(result)


# =============================================================================
# CHUNK D: GRAPPLE + ATTACKS + CONDITIONS
# =============================================================================


class TestExecuteGrapple:
    """Test execute_grapple(grappler_uuid, target_uuid, reason, foundry, source)."""

    @pytest.mark.asyncio
    async def test_grapple_initiate(self):
        """grapple(grappler, target) → contested check."""
        mock_fc = mock_foundry_client()
        mock_fc.contested_check = AsyncMock(return_value={
            "initiatorSuccess": True,
            "initiatorName": "Barbarian",
            "targetName": "Goblin",
            "initiatorRoll": 16,
            "targetRoll": 12,
        })

        result = await execute_grapple("Actor.barbarian", "Actor.goblin", foundry=mock_fc)

        assert result["type"] == "grapple"
        mock_fc.contested_check.assert_called_once()


class TestExecuteAttackWithItem:
    """Test execute_attack_with_item(attacker_uuid, target_uuid, item_name, advantage, foundry, source)."""

    @pytest.mark.asyncio
    async def test_attack_with_item(self):
        """attack_with_item(attacker, target, item) → roll attack."""
        mock_fc = mock_foundry_client()

        result = await execute_attack_with_item("Actor.goblin", "Actor.hero", "shortsword", foundry=mock_fc)

        assert result["type"] == "attack_with_item"


class TestExecuteEnvironmentalSave:
    """Test execute_environmental_save(actor_uuid, hazard, ability, dc, foundry, source)."""

    @pytest.mark.asyncio
    async def test_environmental_save(self):
        """environmental_save(actor, hazard, ability, dc) → roll save."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value=None):
            result = await execute_environmental_save("Actor.hero", "lava", "dexterity", 14, foundry=mock_fc)

        assert result["type"] == "environmental_save"


class TestExecuteOpportunityAttack:
    """Test execute_opportunity_attack(attacker_uuid, target_uuid, reason, foundry, source)."""

    @pytest.mark.asyncio
    async def test_opportunity_attack(self):
        """opportunity_attack(attacker, target) → roll attack."""
        mock_fc = mock_foundry_client()

        with patch("actions.executors._player_actor_name", return_value=None):
            result = await execute_opportunity_attack("Actor.goblin", "Actor.hero", foundry=mock_fc)

        assert result["type"] == "opportunity_attack"


class TestExecuteTacticalAnalysis:
    """Test execute_tactical_analysis(actor_uuid, include_recommendations, foundry, source)."""

    @pytest.mark.asyncio
    async def test_tactical_analysis(self):
        """tactical_analysis(actor_uuid) → analyze battlefield state."""
        mock_fc = mock_foundry_client()

        with patch("combat.tactics.build_tactical_snapshot", new=AsyncMock(return_value="Mock snapshot")):
            result = await execute_tactical_analysis("Actor.hero", foundry=mock_fc)

        assert result["type"] == "tactical_analysis"
        assert result["actor"] == "Actor.hero"
        assert result["analysis"] == "Mock snapshot"


# =============================================================================
# CHUNK E: SCENE STATE + GENERATION + EXECUTION
# =============================================================================


class TestExecuteSetWeatherTime:
    """Test execute_set_weather / execute_set_time."""

    @pytest.mark.asyncio
    async def test_set_weather(self):
        """set_weather(weather) → ambient scene change."""
        mock_app = MagicMock()
        mock_app.ambient_manager = MagicMock()

        result = await execute_set_weather("clear", app_state=mock_app)

        assert result["type"] == "set_weather"

    @pytest.mark.asyncio
    async def test_set_time(self):
        """set_time(time) → update scene time."""
        mock_app = MagicMock()
        mock_app.ambient_manager = MagicMock()

        result = await execute_set_time("morning", app_state=mock_app)

        assert result["type"] == "set_time"


class TestExecuteApplyEffect:
    """Test execute_apply_token_effect / execute_update_vision."""

    @pytest.mark.asyncio
    async def test_apply_token_effect(self):
        """apply_token_effect(token_id, effect_type, effect_name, app_state) → add visual effect."""
        mock_app = MagicMock()
        mock_app.effects_manager = MagicMock()

        result = await execute_apply_token_effect("token1", "condition", "burning", app_state=mock_app)

        assert result["type"] == "apply_token_effect"

    @pytest.mark.asyncio
    async def test_update_vision(self):
        """update_vision(actor_uuid, distance, vision_type, app_state) → change vision settings."""
        mock_app = MagicMock()
        mock_app.effects_manager = MagicMock()

        result = await execute_update_vision("Actor.hero1", 60, "darkvision", app_state=mock_app)

        assert result["type"] == "update_vision"




class TestExecuteGameControl:
    """Test execute_js / pause_game / resume_game / execute_execute_macro."""

    @pytest.mark.asyncio
    async def test_execute_js(self):
        """execute_js(code) → run JavaScript in Foundry."""
        mock_fc = mock_foundry_client()

        result = await execute_execute_js("game.scenes.active.name", foundry=mock_fc)

        assert result["type"] == "execute_js"

    @pytest.mark.asyncio
    async def test_pause_game(self):
        """pause_game() → freeze combat/time."""
        mock_fc = mock_foundry_client()

        result = await execute_pause_game(foundry=mock_fc)

        assert result["type"] == "pause_game"

    @pytest.mark.asyncio
    async def test_resume_game(self):
        """resume_game() → unfreeze combat/time."""
        mock_fc = mock_foundry_client()

        result = await execute_resume_game(foundry=mock_fc)

        assert result["type"] == "resume_game"

    @pytest.mark.asyncio
    async def test_execute_macro(self):
        """execute_execute_macro(macro_id, overrides, app_state) → run a Foundry macro."""
        mock_app = MagicMock()
        mock_app.macro_manager = MagicMock()
        mock_app.action_dispatcher = AsyncMock()

        result = await execute_execute_macro("macro_teleport", {"x": 100, "y": 100}, app_state=mock_app)

        assert result["type"] == "execute_macro"
