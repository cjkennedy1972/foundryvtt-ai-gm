"""actions/executors.py: failure branches, PC-deferral, resolution helpers and scene-building flows
that the existing executor suites leave untested. Foundry is a mock; every assertion is on what the
executor sends to it or returns to the dispatcher."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import actions.executors as ex
from config import settings
from immersion.cinema import CinemaDirector, reading_time


def run(coro):
    return asyncio.run(coro)


ASYNC = ("chat_message", "roll", "execute_js", "get_actors", "get_scene_tokens", "decrease_attribute",
         "increase_attribute", "start_encounter", "end_encounter", "use_spell_slot", "check_spell_ritual",
         "break_concentration", "apply_condition", "contested_check", "get_passive_perception",
         "configure_scene", "clear_canvas_layer", "canvas_create", "place_token", "set_active_scene",
         "wait_for_hook", "upload_file", "create_entity", "add_effect", "request_skill_check")


def F(**over):
    f = MagicMock()
    for name in ASYNC:
        setattr(f, name, AsyncMock(return_value={}))
    f.is_connected = True
    f._get_speaker_name.return_value = "GM"
    f.get_actors.return_value = []
    f.get_scene_tokens.return_value = []
    f.cinema = None
    for k, v in over.items():
        setattr(f, k, v)
    return f


def js(result):
    return AsyncMock(return_value={"result": result})


PC = {"name": "Hero", "uuid": "Actor.hero", "has_player_owner": True}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    ex.reset_action_caches()
    monkeypatch.setattr(settings, "players_roll_own", True)
    yield
    ex.reset_action_caches()


@pytest.fixture
def spawned(monkeypatch):
    got = []
    monkeypatch.setattr(ex, "spawn", lambda c: got.append(c))
    return got


# ── speak / presence ────────────────────────────────────────────────────────

def test_speak_voices_the_npc_with_its_record_and_puts_the_line_on_screen(monkeypatch, spawned):
    f = F()
    f.cinema = CinemaDirector(f)
    record = SimpleNamespace(portrait="npc.png")
    monkeypatch.setattr(ex.tts_playback, "is_active", lambda: True)
    monkeypatch.setattr(ex.tts_playback, "get_npc_record", lambda n: record)
    monkeypatch.setattr(ex.tts_playback, "speak", MagicMock(return_value="TTS"))
    monkeypatch.setattr(f.cinema, "say", MagicMock(return_value="SAY"))
    monkeypatch.setattr(ex, "_ensure_npc_presence", MagicMock(return_value="PRESENCE"))
    out = run(ex.execute_speak("Grum", "Welcome, traveller.", foundry=f))
    assert out["type"] == "speak" and out["npc"] == "Grum"
    f.chat_message.assert_awaited_once_with("Welcome, traveller.", speaker="Grum", whisper=[])
    ex.tts_playback.speak.assert_called_once_with("Welcome, traveller.", "Grum", record, f)
    f.cinema.say.assert_called_once_with("Grum", "Welcome, traveller.", portrait="npc.png", duration_s=reading_time("Welcome, traveller."))
    assert spawned == ["PRESENCE", "TTS", "SAY"]


def test_a_whispered_npc_line_is_private_no_presence_no_subtitle(monkeypatch, spawned):
    f = F()
    f.cinema = CinemaDirector(f)
    monkeypatch.setattr(ex.tts_playback, "is_active", lambda: False)
    run(ex.execute_speak("Grum", "psst", whisper_to="user1", foundry=f))
    f.chat_message.assert_awaited_once_with("psst", speaker="Grum", whisper=["user1"])
    assert spawned == []


def test_speaking_for_a_player_character_is_refused_and_nothing_is_posted():
    f = F()
    f.get_actors.return_value = [PC]
    out = run(ex.execute_speak("Hero", "I draw my sword", foundry=f))
    assert out["success"] is False and "player character" in out["error"]
    f.chat_message.assert_not_called()


def test_speaking_is_refused_when_the_pc_lookup_fails_rather_than_risking_a_pc():
    f = F()
    f.get_actors.side_effect = ConnectionError("relay down")
    out = run(ex.execute_speak("Grum", "hi", foundry=f))
    assert out["success"] is False
    f.chat_message.assert_not_called()


def test_presence_check_runs_once_per_window_and_survives_foundry_errors(monkeypatch):
    f = F(execute_js=js({"ok": True, "revealed": True}))
    run(ex._ensure_npc_presence("Grum", f))
    run(ex._ensure_npc_presence(" GRUM ", f))
    assert f.execute_js.await_count == 1                      # same NPC within the window: not re-queried
    assert 'const want="grum"' in f.execute_js.await_args.args[0]
    monkeypatch.setattr(ex, "_PRESENCE_RECHECK_SECS", 0.0)
    f.execute_js = js({"ok": True, "placed": True})
    run(ex._ensure_npc_presence("Grum", f))
    f.execute_js = js({"ok": False, "reason": "no actor"})
    run(ex._ensure_npc_presence("Grum", f))
    f.execute_js = AsyncMock(side_effect=RuntimeError("js down"))
    run(ex._ensure_npc_presence("Grum", f))                  # must not raise
    f.execute_js = AsyncMock(return_value="junk")
    run(ex._ensure_npc_presence("Grum", f))


# ── pure helpers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("formula,adv,expected", [
    ("1d20+3", True, "2d20kh1+3"), ("1d20+3", False, "2d20kl1+3"), ("d20", True, "2d20kh1"),
    ("1d20+3", None, "1d20+3"), ("5", True, "5"), ("", True, ""), (None, True, None),
    ("  1D20-1", False, "2d20kl1-1"),
])
def test_advantage_rewrites_a_leading_die_into_one_foundry_roll(formula, adv, expected):
    assert ex._advantage_formula(formula, adv) == expected


def test_is_player_character_semantics_known_empty_world_and_failures():
    f = F()
    assert run(ex._is_player_character("", f)) is None and run(ex._is_player_character("x", None)) is None
    assert run(ex._is_player_character("Hero", f)) is False         # lookup succeeded, no PCs: a definite "no"
    ex.reset_action_caches()
    f.get_actors.return_value = [PC, {"name": "Orc"}]
    assert run(ex._is_player_character(" hero ", f)) is True
    assert run(ex._is_player_character("Orc", f)) is False
    assert f.get_actors.await_count == 2                              # one per reset; the Orc lookup was served from cache
    ex.reset_action_caches()
    f.get_actors.side_effect = ConnectionError("down")
    assert run(ex._is_player_character("Hero", f)) is None
    f.get_actors.side_effect = None
    f.get_actors.return_value = [PC]
    assert run(ex._is_player_character("Hero", f)) is True            # a failure is not cached as "no PCs"


def test_reset_caches_survives_a_poisoned_pc_cache():
    ex._pc_names_cache = None
    ex.reset_action_caches()
    assert ex._pc_names_cache == set()


TOKENS = [{"id": "t1", "name": "Goblin", "actorUuid": "Actor.g1"}, {"id": "t2", "name": "Orc", "actorUuid": ""}]


def test_token_resolution_prefers_real_ids_then_actor_uuid_then_name():
    f = F()
    f.get_scene_tokens.return_value = TOKENS
    assert run(ex._resolve_token_id("t2", f)) == "t2"
    assert run(ex._resolve_token_id("Actor.g1", f)) == "t1"
    assert run(ex._resolve_token_id("g1", f)) == "t1"                 # trailing id segment
    assert run(ex._resolve_token_id("orc", f)) == "t2"
    assert run(ex._resolve_token_id("Dragon", f)) == "Dragon"         # unresolved: passed through so the error surfaces
    assert run(ex._resolve_token_id("  ", f)) == ""


def test_token_resolution_falls_back_to_the_identifier_when_the_scene_cannot_be_read():
    f = F()
    f.get_scene_tokens.side_effect = ConnectionError("x")
    assert run(ex._resolve_token_id("Goblin", f)) == "Goblin"


ACTORS = [{"name": "Goblin", "uuid": "Actor.g1", "hp": 7, "max_hp": 10}, {"name": "Elf", "uuid": "Actor.e1", "hp": "bad", "max_hp": None}]


def test_actor_resolution_by_uuid_name_then_short_id():
    f = F()
    f.get_actors.return_value = ACTORS
    assert run(ex._resolve_actor_uuid("Actor.g1", f)) == "Actor.g1"
    assert run(ex._resolve_actor_uuid("elf", f)) == "Actor.e1"
    assert run(ex._resolve_actor_uuid("Scene.x.e1", f)) == "Actor.e1"
    assert run(ex._resolve_actor_uuid("nobody", f)) is None
    assert run(ex._resolve_actor_uuid("", f)) is None
    f.get_actors.side_effect = ConnectionError("x")
    assert run(ex._resolve_actor_uuid("elf", f)) is None


def test_read_hp_returns_unknown_rather_than_a_wrong_number():
    f = F()
    f.get_actors.return_value = ACTORS
    assert run(ex._read_hp(f, "Actor.g1")) == (7, 10)
    assert run(ex._read_hp(f, "actor.E1")) == (None, None)            # "bad" hp and missing max are unknown, not 0
    assert run(ex._read_hp(f, "Actor.zzz")) == (None, None)
    f.get_actors.side_effect = ConnectionError("x")
    assert run(ex._read_hp(f, "Actor.g1")) == (None, None)


# ── update_hp ───────────────────────────────────────────────────────────────

def _hp_foundry(before, after, first_error=None):
    f = F()
    seen = iter([before, after, after])
    f.get_actors.side_effect = lambda **kw: [{"uuid": "Actor.g1", "name": "Goblin", "hp": next(seen), "max_hp": 10}]
    f.decrease_attribute.side_effect = first_error
    return f


def test_a_lost_reply_is_reported_as_success_when_the_sheet_shows_the_damage_landed():
    f = _hp_foundry(10, 7, first_error=ConnectionError("reply lost"))
    out = run(ex.execute_update_hp("Actor.g1", 3, foundry=f))
    assert out["success"] is True and out["verified_after_lost_reply"] is True and (out["hp_before"], out["hp_after"]) == (10, 7)
    assert f.decrease_attribute.await_count == 1                      # never re-applied


def test_a_failed_write_that_did_not_land_is_reported_not_retried():
    f = _hp_foundry(10, 10, first_error=ConnectionError("down"))
    out = run(ex.execute_update_hp("Actor.g1", 3, foundry=f))
    assert out["success"] is False and "did not apply" in out["error"] and f.decrease_attribute.await_count == 1


def test_an_unexpected_hp_change_after_a_failed_write_blocks_a_retry():
    f = _hp_foundry(10, 4, first_error=ConnectionError("down"))
    out = run(ex.execute_update_hp("Actor.g1", 3, foundry=f))
    assert out["success"] is False and "unexpected amount (10→4, expected 7)" in out["error"]
    assert f.decrease_attribute.await_count == 1


def test_killing_blows_are_clamped_at_zero_when_verifying():
    f = _hp_foundry(5, 0, first_error=ConnectionError("down"))
    assert run(ex.execute_update_hp("Actor.g1", 50, foundry=f))["verified_after_lost_reply"] is True


def test_overheal_is_clamped_to_max_when_verifying():
    f = F()
    seq = iter([8, 10, 10])
    f.get_actors.side_effect = lambda **kw: [{"uuid": "Actor.g1", "hp": next(seq), "max_hp": 10}]
    f.increase_attribute.side_effect = ConnectionError("down")
    assert run(ex.execute_update_hp("Actor.g1", -5, foundry=f))["success"] is True


def test_an_unreadable_sheet_falls_back_to_a_conservative_failure():
    f = F()
    f.get_actors.return_value = [{"uuid": "Actor.g1", "name": "Goblin"}]
    f.decrease_attribute.side_effect = ConnectionError("down")
    out = run(ex.execute_update_hp("Actor.g1", 3, foundry=f))
    assert out["success"] is False and "failed transiently; not retried" in out["error"]


def test_a_hallucinated_uuid_is_resolved_by_name_and_retried_once_against_the_real_uuid():
    f = F()
    f.get_actors.return_value = [{"uuid": "Actor.g1", "name": "Goblin", "hp": 9, "max_hp": 10}]
    f.decrease_attribute.side_effect = [{"success": False}, {"ok": True}]
    out = run(ex.execute_update_hp("Goblin", 4, foundry=f))
    assert out["actor_uuid"] == "Actor.g1" and out["result"] == {"ok": True}
    assert [c.args for c in f.decrease_attribute.await_args_list] == [("hp.value", 4, "Goblin"), ("hp.value", 4, "Actor.g1")]
    f2 = F()
    f2.decrease_attribute.side_effect = [{"success": False}]
    out2 = run(ex.execute_update_hp("Nobody", 4, foundry=f2))
    assert out2["success"] is False and "No actor matches 'Nobody'" in out2["error"]


def test_healing_uses_increase_with_the_absolute_amount():
    f = F()
    f.increase_attribute.return_value = {"ok": True}
    run(ex.execute_update_hp("Actor.g1", -6, foundry=f))
    f.increase_attribute.assert_awaited_once_with("hp.value", 6, "Actor.g1")
    f.decrease_attribute.assert_not_called()


# ── encounters ──────────────────────────────────────────────────────────────

def _app(**kw):
    tracker = MagicMock()
    tracker.set_combat_mode = AsyncMock()
    return SimpleNamespace(state_tracker=tracker, **kw)


def test_start_encounter_discovers_scene_tokens_starts_combat_and_syncs_state():
    f = F()
    f.get_scene_tokens.return_value = [{"id": "t1"}, {"name": "no id"}, {"id": "t2"}]
    app = _app()
    out = run(ex.execute_start_encounter(None, "Ambush", foundry=f, auto_roll_initiative=False, app_state=app))
    f.start_encounter.assert_awaited_once_with(tokens=["t1", "t2"], roll_all=False, name="Ambush")
    app.state_tracker.set_combat_mode.assert_awaited_once_with(in_combat=True, turn_order=["t1", "t2"])
    assert out["success"] is True and out["token_ids"] == ["t1", "t2"]


@pytest.mark.parametrize("tokens", [[{"name": "ghost"}], []])
def test_start_encounter_refuses_an_empty_scene_without_calling_foundry(tokens):
    f = F()
    f.get_scene_tokens.return_value = tokens
    out = run(ex.execute_start_encounter(None, foundry=f))
    assert out["success"] is False and "No tokens found" in out["error"]
    f.start_encounter.assert_not_called()


def test_start_encounter_treats_an_unreadable_scene_as_empty():
    f = F()
    f.get_scene_tokens.side_effect = ConnectionError("x")
    assert run(ex.execute_start_encounter(None, foundry=f))["success"] is False


def test_end_encounter_returns_the_tracker_to_exploration():
    app = _app()
    run(ex.execute_end_encounter(foundry=F(), app_state=app))
    app.state_tracker.set_combat_mode.assert_awaited_once_with(in_combat=False)


# ── cast_spell ──────────────────────────────────────────────────────────────

def _caster(conflict=None, **over):
    f = F(execute_js=js(conflict or {}))
    f.use_spell_slot.return_value = {"ok": True, "used": True, "remaining": 1}
    f.check_spell_ritual.return_value = {"isRitual": True}
    for k, v in over.items():
        setattr(f, k, v)
    return f


def test_a_cantrip_is_cast_without_touching_slots_even_though_no_level_zero_pool_exists():
    # The real use_spell_slot answers {"ok": True, "used": False, "remaining": 0} for spell level 0.
    f = _caster()
    f.use_spell_slot.return_value = {"ok": True, "used": False, "remaining": 0}
    out = run(ex.execute_cast_spell("Actor.w", "Fire Bolt", 0, foundry=f))
    assert "error" not in out and out.get("success", True) is not False
    f.use_spell_slot.assert_not_called()


def test_a_cantrip_does_not_strip_concentration_unless_it_requires_it():
    f = _caster(conflict={"newSpellRequiresConcentration": True, "alreadyConcentrating": True, "concentratingOn": "Bless"})
    out = run(ex.execute_cast_spell("Actor.w", "Guidance", 0, foundry=f))
    f.break_concentration.assert_awaited_once_with("Actor.w")
    assert "Bless" in out["concentration_note"]


def test_cantrips_cannot_be_ritual_cast_and_non_ritual_spells_are_refused():
    f = _caster()
    out = run(ex.execute_cast_spell("Actor.w", "Fire Bolt", 0, ritual=True, foundry=f))
    assert out["success"] is False and out["error"] == "Cantrips cannot be cast as rituals"
    f.check_spell_ritual.return_value = {"isRitual": False}
    out = run(ex.execute_cast_spell("Actor.w", "Fireball", 3, ritual=True, foundry=f))
    assert out["success"] is False and "not marked as a ritual" in out["error"]
    f.use_spell_slot.assert_not_called()


def test_a_ritual_cast_consumes_no_slot_and_a_failed_ritual_check_refuses():
    f = _caster()
    out = run(ex.execute_cast_spell("Actor.w", "Detect Magic", 1, ritual=True, foundry=f))
    assert out["ritual"] is True and out["result"]["used"] is False
    f.use_spell_slot.assert_not_called()
    f.check_spell_ritual.side_effect = RuntimeError("js down")
    assert run(ex.execute_cast_spell("Actor.w", "Detect Magic", 1, ritual=True, foundry=f))["success"] is False


def test_a_failed_concentration_probe_does_not_block_the_cast():
    f = _caster(execute_js=AsyncMock(side_effect=RuntimeError("js down")))
    out = run(ex.execute_cast_spell("Actor.w", "Fireball", 3, foundry=f))
    assert out.get("success", True) is not False and "concentration_note" not in out
    f.use_spell_slot.assert_awaited_once_with("Actor.w", 3)


def test_breaking_concentration_failing_still_reports_the_cast_and_the_note():
    f = _caster(conflict={"newSpellRequiresConcentration": True, "alreadyConcentrating": True})
    f.break_concentration.side_effect = RuntimeError("relay")
    out = run(ex.execute_cast_spell("Actor.w", "Hold Person", 2, foundry=f))
    assert "their previous spell" in out["concentration_note"]


def test_no_slot_left_fails_the_cast_and_keeps_concentration():
    f = _caster(conflict={"newSpellRequiresConcentration": True, "alreadyConcentrating": True, "concentratingOn": "Bless"})
    f.use_spell_slot.return_value = {"ok": True, "used": False, "remaining": 0}
    out = run(ex.execute_cast_spell("Actor.w", "Hold Person", 2, foundry=f))
    assert out["success"] is False and "no level 2 spell slot left (0 remaining)" in out["error"]
    f.break_concentration.assert_not_called()


def test_a_stubbed_older_slot_result_shape_still_counts_by_truthiness():
    f = _caster()
    f.use_spell_slot.return_value = {"success": True}
    assert run(ex.execute_cast_spell("Actor.w", "Shield", 1, foundry=f)).get("success", True) is not False
    f.use_spell_slot.return_value = None
    assert run(ex.execute_cast_spell("Actor.w", "Shield", 1, foundry=f))["success"] is False


# ── PC lookup + checks that defer to players ────────────────────────────────

def test_player_actor_name_matches_full_uuid_or_short_id_and_survives_lookup_failure(caplog):
    f = F()
    f.get_actors.return_value = [PC]
    assert run(ex._player_actor_name("ACTOR.HERO", f)) == "Hero"
    assert run(ex._player_actor_name("hero", f)) == "Hero"            # bare id
    assert run(ex._player_actor_name("Actor.orc", f)) is None
    assert run(ex._player_actor_name("", f)) is None
    ex.reset_action_caches()
    f.get_actors.side_effect = ConnectionError("x")
    assert run(ex._player_actor_name("Actor.hero", f)) is None


def test_a_death_save_status_needs_hp_to_be_trusted():
    f = F(execute_js=js({"hp": 0, "isDead": False, "isStable": False, "successes": 1, "failures": 2}))
    assert run(ex.get_death_save_status("Actor.h", f))["failures"] == 2
    assert run(ex.get_death_save_status("Actor.h", F(execute_js=js({"hp": None})))) is None
    assert run(ex.get_death_save_status("Actor.h", F(execute_js=js("junk")))) is None
    assert run(ex.get_death_save_status("Actor.h", F(execute_js=AsyncMock(side_effect=RuntimeError("x"))))) is None


def test_death_saves_for_a_pc_prompt_the_player_with_the_advantage_state():
    f = F()
    f.get_actors.return_value = [PC]
    out = run(ex.execute_death_save("Actor.hero", advantage=False, foundry=f))
    assert out["deferred_to_player"] is True
    msg = f.chat_message.await_args.args[0]
    assert "Hero" in msg and "with disadvantage" in msg
    assert not f.request_death_save.called


def test_attack_with_an_item_for_a_pc_is_deferred_not_auto_rolled():
    f = F()
    f.get_actors.return_value = [PC]
    out = run(ex.execute_attack_with_item("Actor.hero", "Longsword", "t1", foundry=f))
    assert out["deferred_to_player"] is True and "Longsword" in f.chat_message.await_args.args[0]
    f.execute_js.assert_not_called()


# ── apply_condition / exhaustion / inspiration / passive ────────────────────

def test_apply_condition_records_whether_the_condition_was_already_present_for_undo():
    f = F(execute_js=js({"ok": True, "present": True}))
    out = run(ex.execute_apply_condition("Actor.g", "Prone", "1 round", foundry=f))
    assert out["had_condition"] is True
    f.apply_condition.assert_awaited_once_with("Actor.g", "Prone", "1 round")
    assert '"prone"' in f.execute_js.await_args.args[0]               # probed with the lower-cased status id
    f2 = F(execute_js=AsyncMock(side_effect=RuntimeError("x")))
    assert run(ex.execute_apply_condition("Actor.g", "Prone", foundry=f2))["had_condition"] is None
    f3 = F(execute_js=js({"ok": False}))
    assert run(ex.execute_apply_condition("Actor.g", "Prone", foundry=f3))["had_condition"] is None


def test_exhaustion_announces_gains_and_recoveries_but_not_no_ops_and_reports_failures():
    f = F(execute_js=js({"ok": True, "previousLevel": 1, "newLevel": 2}))
    out = run(ex.execute_set_exhaustion("Actor.g", 1, reason="forced march", foundry=f))
    assert out["success"] is True and out["newLevel"] == 2
    assert f.chat_message.await_args.args[0] == "😮‍💨 Exhaustion gains level 2 (forced march)."
    f = F(execute_js=js({"ok": True, "previousLevel": 3, "newLevel": 2}))
    run(ex.execute_set_exhaustion("Actor.g", -1, foundry=f))
    assert "recovers to level 2" in f.chat_message.await_args.args[0]
    f = F(execute_js=js({"ok": True, "previousLevel": 6, "newLevel": 6}))
    run(ex.execute_set_exhaustion("Actor.g", 1, foundry=f))
    f.chat_message.assert_not_called()
    bad = run(ex.execute_set_exhaustion("Actor.g", 1, foundry=F(execute_js=js({"ok": False, "error": "actor not found"}))))
    assert bad == {"type": "set_exhaustion", "success": False, "error": "actor not found"}
    assert run(ex.execute_set_exhaustion("Actor.g", 1, foundry=F(execute_js=AsyncMock(return_value="junk"))))["error"] == "junk"


def test_inspiration_is_announced_once_and_failures_are_reported():
    f = F(execute_js=js({"ok": True, "alreadyHad": False}))
    f.get_actors.return_value = [PC]
    out = run(ex.execute_grant_inspiration("Actor.hero", "great roleplay", foundry=f))
    assert out["success"] is True and f.chat_message.await_args.args[0] == "✨ **Hero** gains Heroic Inspiration — great roleplay!"
    f = F(execute_js=js({"ok": True, "alreadyHad": True}))
    run(ex.execute_grant_inspiration("Actor.x", foundry=f))
    f.chat_message.assert_not_called()
    bad = run(ex.execute_grant_inspiration("Actor.x", foundry=F(execute_js=js({"ok": False, "error": "nope"}))))
    assert bad == {"type": "grant_inspiration", "success": False, "error": "nope"}
    assert run(ex.execute_grant_inspiration("Actor.x", foundry=F(execute_js=js(None))))["error"] == "{'result': None}"


def test_only_perception_is_resolved_passively():
    f = F()
    out = run(ex.execute_passive_check("Actor.g", "Stealth", 12, foundry=f))
    assert "skill_check" in out["note"] and "success" not in out
    f.chat_message.assert_not_called()


@pytest.mark.parametrize("score,dc,ok", [(14, 14, True), (13, 14, False)])
def test_passive_perception_compares_against_the_dc_inclusively(score, dc, ok):
    f = F()
    f.get_actors.return_value = [PC]
    f.get_passive_perception.return_value = {"passivePerception": score}
    out = run(ex.execute_passive_check("Actor.hero", "Perception", dc, reason="ambush", foundry=f))
    assert out["success"] is ok and out["passive_score"] == score
    msg = f.chat_message.await_args.args[0]
    assert f"**Hero** passive Perception: {score} vs DC {dc} (ambush)" in msg and ("Success" in msg) is ok


def test_passive_perception_defaults_to_ten_when_foundry_cannot_say():
    f = F()
    f.get_passive_perception.side_effect = RuntimeError("x")
    out = run(ex.execute_passive_check("Actor.orc", "prc", 10, foundry=f))
    assert out["passive_score"] == 10 and out["success"] is True
    f.get_passive_perception.side_effect = None
    f.get_passive_perception.return_value = "junk"
    assert run(ex.execute_passive_check("Actor.orc", "prc", 11, foundry=f))["passive_score"] == 10


# ── grapple ─────────────────────────────────────────────────────────────────

def test_grapple_success_applies_the_condition_and_tolerates_the_condition_failing():
    f = F()
    f.contested_check.return_value = {"initiatorName": "Orc", "targetName": "Elf", "initiatorRoll": 17, "targetRoll": 9, "initiatorSuccess": True}
    f.apply_condition.side_effect = RuntimeError("relay")
    out = run(ex.execute_grapple("Actor.o", "Actor.e", reason="in the doorway", foundry=f))
    assert out["success"] is True and (out["grappler_roll"], out["target_roll"]) == (17, 9)
    msg = f.chat_message.await_args.args[0]
    assert "Grapple succeeds" in msg and msg.endswith("(in the doorway)")
    f.apply_condition.assert_awaited_once_with("Actor.e", "grappled")


def test_grapple_failure_takes_no_mechanical_action():
    f = F()
    f.contested_check.return_value = {"initiatorSuccess": False}
    out = run(ex.execute_grapple("Actor.o", "Actor.e", foundry=f))
    assert out["success"] is False and "Grapple fails" in f.chat_message.await_args.args[0]
    f.apply_condition.assert_not_called()


def test_grapple_surfaces_contest_errors_and_exceptions_as_data():
    f = F()
    f.contested_check.return_value = {"error": "no actor"}
    assert run(ex.execute_grapple("a", "b", foundry=f)) == {"type": "grapple", "success": False, "error": "no actor"}
    f.contested_check.side_effect = RuntimeError("boom")
    assert run(ex.execute_grapple("a", "b", foundry=f)) == {"type": "grapple", "success": False, "error": "boom"}


# ── save items / environmental saves ────────────────────────────────────────

def _scene_with_pc():
    f = F()
    f.get_actors.return_value = [PC]
    f.get_scene_tokens.return_value = [{"id": "tp", "actorUuid": "Actor.hero"}, {"id": "tn", "actorUuid": "Actor.orc"}]
    return f


def test_player_targets_are_split_out_of_auto_resolution():
    f = _scene_with_pc()
    resolved, auto, deferred = run(ex._split_targets_by_ownership(["tp", "tn"], f))
    assert (resolved, auto, deferred) == (["tp", "tn"], ["tn"], ["Hero"])
    f.get_scene_tokens.side_effect = RuntimeError("x")
    assert run(ex._split_targets_by_ownership(["tp"], f))[1] == ["tp"]     # unreadable scene: nobody can be shown to be a PC
    settings.players_roll_own = False
    assert run(ex._split_targets_by_ownership(["tp"], _scene_with_pc()))[1] == ["tp"]


def test_use_save_item_defers_pcs_and_returns_the_npc_results():
    f = _scene_with_pc()
    f.execute_js = js({"ok": True, "ability": "dex", "dc": 15, "results": [{"token": "tn", "success": False}, {"token": "tp", "deferred": True}]})
    out = run(ex.execute_use_save_item("Actor.dragon", "Fire Breath", ["tp", "tn"], foundry=f))
    assert out["success"] is True and out["deferred_players"] == ["Hero"]
    assert f.chat_message.await_args.args[0].startswith("🎲 **Hero**, make a **Dex** saving throw (DC 15) against Fire Breath!")
    script = f.execute_js.await_args.args[0]
    assert '["tp", "tn"]' in script and '["tn"]' in script


def test_use_save_item_reports_script_errors_without_posting_chat():
    f = _scene_with_pc()
    f.execute_js = AsyncMock(side_effect=RuntimeError("js down"))
    assert run(ex.execute_use_save_item("Actor.d", "Fire Breath", ["tn"], foundry=f)) == {
        "type": "use_save_item", "item": "Fire Breath", "success": False, "error": "js down"}
    f.execute_js = js({"ok": False, "error": "no such item"})
    assert run(ex.execute_use_save_item("Actor.d", "Fire Breath", ["tn"], foundry=f))["error"] == "no such item"
    f.chat_message.assert_not_called()


def test_environmental_save_defers_pcs_with_the_reason_and_reports_failures():
    f = _scene_with_pc()
    f.execute_js = js({"ok": True, "results": [{"token": "tn", "success": True}]})
    out = run(ex.execute_environmental_save("con", 13, ["tp", "tn"], damage_formula="2d6", reason="poison gas", foundry=f))
    assert out["deferred_players"] == ["Hero"] and out["success"] is True
    assert f.chat_message.await_args.args[0] == "🎲 **Hero**, make a **Con** saving throw (DC 13) — poison gas. (Roll from your sheet, or tell me your result.)"
    f.execute_js = js({"ok": False, "error": "bad formula"})
    bad = run(ex.execute_environmental_save("con", 13, ["tn"], foundry=f))
    assert bad == {"type": "environmental_save", "ability": "con", "dc": 13, "success": False, "error": "bad formula"}
    f.execute_js = AsyncMock(side_effect=RuntimeError("js down"))
    assert run(ex.execute_environmental_save("con", 13, ["tn"], foundry=f))["error"] == "js down"


# ── opportunity attack ──────────────────────────────────────────────────────

def test_opportunity_attack_uses_the_first_real_attack_item_and_reports_its_outcome():
    f = F()
    seq = iter([{"result": ["Scimitar", "Bow"]}, {"result": {"ok": True, "hit": False, "attackTotal": 9, "targetAc": 15}}])
    f.execute_js = AsyncMock(side_effect=lambda s: next(seq))
    out = run(ex.execute_opportunity_attack("Actor.orc", "t1", reason="fled", foundry=f))
    assert out["item"] == "Scimitar" and out["success"] is True and out["result"]["hit"] is False
    assert "scimitar" in f.execute_js.await_args.args[0].lower()


def test_opportunity_attack_without_a_weapon_or_with_unreadable_items_fails_cleanly():
    f = F(execute_js=js([]))
    out = run(ex.execute_opportunity_attack("Actor.orc", "t1", foundry=f))
    assert out["success"] is False and "no weapon" in out["error"]
    f.execute_js = AsyncMock(side_effect=RuntimeError("x"))
    assert run(ex.execute_opportunity_attack("Actor.orc", "t1", foundry=f))["success"] is False


def test_opportunity_attack_by_a_pc_prompts_the_player():
    f = F()
    f.get_actors.return_value = [PC]
    out = run(ex.execute_opportunity_attack("Actor.hero", "t1", reason="retreat", foundry=f))
    assert out["deferred_to_player"] is True and "retreat" in f.chat_message.await_args.args[0]


# ── ambient / effects / vision ──────────────────────────────────────────────

def test_weather_and_time_need_a_manager_and_reject_unknown_values():
    assert run(ex.execute_set_weather("rain", app_state=None)) == {"type": "set_weather", "error": "Ambient manager not available"}
    assert run(ex.execute_set_time("night", app_state=SimpleNamespace())) == {"type": "set_time", "error": "Ambient manager not available"}
    app = SimpleNamespace(ambient_manager=MagicMock())
    assert run(ex.execute_set_weather("Hail of frogs", app_state=app))["error"] == "Unknown weather type: Hail of frogs"
    assert run(ex.execute_set_time("teatime", app_state=app))["error"] == "Unknown time: teatime"
    app.ambient_manager.set_weather.assert_not_called()
    run(ex.execute_set_weather("FOG", app_state=app))
    assert app.ambient_manager.set_weather.call_args.args[0].value == "fog"


def test_token_effects_need_a_manager_and_a_known_effect_type():
    assert run(ex.execute_apply_token_effect("t1", "condition", "prone", app_state=None))["error"] == "Effects manager not available"
    app = SimpleNamespace(effects_manager=MagicMock())
    assert run(ex.execute_apply_token_effect("t1", "curse", "x", app_state=app))["error"] == "Unknown effect type: curse"


def test_a_condition_effect_is_rendered_through_the_actor_and_failures_are_reported():
    app = SimpleNamespace(effects_manager=MagicMock())
    f = F()
    f.get_scene_tokens.return_value = [{"id": "t1", "actorUuid": "Actor.g"}]
    out = run(ex.execute_apply_token_effect("t1", "condition", " Prone ", app_state=app, foundry=f))
    assert out["rendered_in_foundry"] is True
    f.add_effect.assert_awaited_once_with("Actor.g", "prone")
    f.add_effect.side_effect = RuntimeError("relay")
    assert "RuntimeError: relay" in run(ex.execute_apply_token_effect("t1", "condition", "prone", app_state=app, foundry=f))["error"]
    out = run(ex.execute_apply_token_effect("tX", "condition", "prone", app_state=app, foundry=f))
    assert out["rendered_in_foundry"] is False and "No actor found for token tX" in out["error"]
    f.get_scene_tokens.side_effect = RuntimeError("x")
    assert run(ex._actor_uuid_for_token("t1", f)) is None


def test_auras_stay_a_record_and_say_so():
    app = SimpleNamespace(effects_manager=MagicMock())
    out = run(ex.execute_apply_token_effect("t1", "aura", "fire", app_state=app, foundry=F()))
    assert out["rendered_in_foundry"] is False and "error" not in out


def test_vision_updates_require_a_manager_and_report_foundry_failures():
    assert run(ex.execute_update_vision("t1", 60, app_state=None))["error"] == "Vision manager not available"
    vm = MagicMock()
    vm.set_vision_range.return_value = {"range": 60}
    vm.apply_light_source.return_value = {"radius": 20}
    app = SimpleNamespace(vision_manager=vm)
    f = F(execute_js=js({"ok": True}))
    out = run(ex.execute_update_vision("t1", 60, has_light=True, light_radius=20, app_state=app, foundry=f))
    assert out["rendered_in_foundry"] is True and out["result"]["light"] == {"radius": 20}
    f.execute_js = js({"ok": False, "error": "token not found"})
    assert run(ex.execute_update_vision("t1", 60, app_state=app, foundry=f))["error"] == "token not found"
    f.execute_js = js("junk")
    assert run(ex.execute_update_vision("t1", 60, app_state=app, foundry=f))["error"] == "vision update failed"
    f.execute_js = AsyncMock(side_effect=RuntimeError("js down"))
    assert run(ex.execute_update_vision("t1", 60, app_state=app, foundry=f))["error"] == "RuntimeError: js down"


# ── scene building ──────────────────────────────────────────────────────────

def test_place_walls_maps_the_legacy_sense_field_to_sight_without_overriding_an_explicit_sight():
    f = F()
    walls = [{"c": [0, 0, 1, 1], "sense": 0}, {"c": [1, 1, 2, 2], "sense": 0, "sight": 20}]
    run(ex.execute_place_walls(walls, foundry=f))
    assert f.canvas_create.await_args.args == ("walls", [{"c": [0, 0, 1, 1], "sight": 0}, {"c": [1, 1, 2, 2], "sight": 20}])


@pytest.mark.parametrize("fn,layer,kind", [(ex.execute_place_walls, "walls", "walls"), (ex.execute_place_lights, "lights", "lights"),
                                            (ex.execute_place_sounds, "sounds", "sounds")])
def test_a_failed_clear_does_not_stop_the_placement_and_a_clear_happens_first(fn, layer, kind):
    f = F()
    items = [{"x": 1}]
    order = []
    f.clear_canvas_layer.side_effect = lambda l: order.append(("clear", l)) or (_ for _ in ()).throw(RuntimeError("busy"))
    f.canvas_create.side_effect = lambda k, d: order.append(("create", k)) or {"created": 1}
    out = run(fn(items, clear_existing=True, foundry=f))
    assert order == [("clear", layer), ("create", kind)] and out["count"] == 1


def test_place_token_tracks_the_scene_the_relay_reports_and_ignores_tracking_errors():
    f = F()
    f.place_token.return_value = {"sceneId": "S1"}
    out = run(ex.execute_place_token("Goblin", 100, 200, disposition=-1, hidden=True, foundry=f))
    f.place_token.assert_awaited_once_with("Goblin", 100, 200, disposition=-1, hidden=True, uuid=None)
    f._track_scene.assert_called_once_with("S1")
    assert out["actor"] == "Goblin"
    f._track_scene.side_effect = RuntimeError("x")
    run(ex.execute_place_token("Goblin", foundry=f))      # must not raise


def test_configure_scene_maps_arguments_to_foundry_fields_and_always_pins_token_vision():
    f = F()
    out = run(ex.execute_configure_scene(darkness=0.5, global_illumination=True, fog_exploration=False, grid_size=70, scene_name="Crypt", foundry=f))
    expected = {"darkness": 0.5, "globalLight": True, "fogExploration": False, "tokenVision": False, "grid": {"size": 70}}
    assert out["updates"] == expected
    f.configure_scene.assert_awaited_once_with(expected, scene_name="Crypt")
    run(ex.execute_configure_scene(tokenVision=True, foundry=f))
    assert f.configure_scene.await_args.args[0] == {"tokenVision": True}


def test_setup_scene_runs_each_stage_and_records_results(monkeypatch):
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(ex.asyncio, "sleep", sleep)
    f = F()
    f.wait_for_hook.return_value = False                 # canvasReady never fired: fall back to a short sleep
    f.place_token.side_effect = [{"ok": 1}, {"error": "no actor"}, RuntimeError("boom")]
    app = SimpleNamespace(scene_awareness=MagicMock())
    app.scene_awareness.on_scene_change = AsyncMock()
    out = run(ex.execute_setup_scene(
        scene_name="Crypt", background_src="bg.png", walls=[{"c": [0, 0, 1, 1], "sense": 0}], lights=[{"x": 1}], sounds=[{"x": 2}],
        tokens=[{"actor_name": "A"}, {"name": "B", "x": 5}, {"actor_name": "C"}, {"x": 1}], darkness=0.2, grid_size=64,
        clear_walls=True, clear_lights=True, clear_tokens=True, narrate="The door creaks.", foundry=f, app_state=app))
    r = out["results"]
    assert sleeps == [1] and r["scene_switch"] == "ok" and r["background"] == "bg.png"
    assert r["scene_config"] == {"darkness": 0.2, "tokenVision": False, "grid": {"size": 64}}
    assert (r["walls"], r["lights"], r["sounds"], r["tokens"], r["narrated"]) == (1, 1, 1, 1, True)
    assert [c.args[0] for c in f.clear_canvas_layer.await_args_list] == ["walls", "lights", "tokens"]
    assert f.canvas_create.await_args_list[0].args == ("walls", [{"c": [0, 0, 1, 1], "sight": 0}])
    app.scene_awareness.on_scene_change.assert_awaited_once_with("Crypt")
    assert f.place_token.await_count == 3                # the entry with no name is skipped


def test_setup_scene_records_stage_errors_and_carries_on():
    f = F()
    f.set_active_scene.side_effect = RuntimeError("no scene")
    f.configure_scene.side_effect = RuntimeError("cfg")
    f.canvas_create.side_effect = RuntimeError("create failed")
    f.clear_canvas_layer.side_effect = RuntimeError("clear failed")
    f.chat_message.side_effect = RuntimeError("chat down")
    out = run(ex.execute_setup_scene(scene_name="Crypt", background_src="bg", walls=[{}], lights=[{}], sounds=[{}], tokens=[{"name": "A"}],
                                     clear_tokens=True, narrate="hi", foundry=f))
    r = out["results"]
    assert out["success"] is True
    assert r["scene_config_error"] == "cfg" and r["walls_error"] == "create failed" and r["lights_error"] == "create failed"
    assert r["sounds_error"] == "create failed" and r["tokens_error"] == "clear failed" and "narrated" not in r
    assert "scene_switch" not in r and "background" not in r


# ── generate_map ────────────────────────────────────────────────────────────

def _mapgen(tmp_path, results, hires=1):
    out = tmp_path / "map.png"
    out.write_bytes(b"PNG")
    gen = MagicMock()
    gen.hires_scale = hires
    seq = iter([{**r, "output_file": str(out)} if r.get("status") == "success" else r for r in results])
    gen.generate_map = AsyncMock(side_effect=lambda **kw: next(seq))
    return SimpleNamespace(map_generator=gen, map_output_dir=str(tmp_path)), gen


def test_generate_map_needs_a_generator():
    assert "not available" in run(ex.execute_generate_map("p", "S", app_state=None, foundry=F()))["error"]
    assert "not available" in run(ex.execute_generate_map("p", "S", app_state=SimpleNamespace(map_generator=None), foundry=F()))["error"]


def test_generate_map_creates_a_grid_aligned_scene_from_the_uploaded_art(tmp_path, monkeypatch):
    async def nosleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", nosleep)
    app, gen = _mapgen(tmp_path, [{"status": "success"}], hires=2)
    f = F()
    f.upload_file.return_value = {"path": "worlds/maps/map.png"}
    f.create_entity.return_value = {"data": {"_id": "sc1"}}
    out = run(ex.execute_generate_map("a crypt", "Crypt", size="small", narration="You descend.", app_state=app, foundry=f))
    assert out["success"] is True and out["dimensions"] == {"width": 2048, "height": 1536} and out["background"] == "worlds/maps/map.png"
    scene = f.create_entity.await_args.args[1]
    assert f.create_entity.await_args.args[0] == "Scene"
    assert scene["grid"] == {"size": 128, "padding": 0} and scene["padding"] == 0 and scene["background"] == {"src": "worlds/maps/map.png"}
    assert scene["width"] % scene["grid"]["size"] == 0 and scene["height"] % scene["grid"]["size"] == 0
    f.set_active_scene.assert_awaited_once_with("Crypt")
    f.chat_message.assert_awaited()                       # the narration went out
    assert gen.generate_map.await_args.kwargs["width"] == 1024 and gen.generate_map.await_args.kwargs["style"] == "battlemap"


def test_generate_map_retries_a_failed_generation_once_but_not_an_unreachable_backend(tmp_path):
    app, gen = _mapgen(tmp_path, [{"status": "error", "error": "oom"}, {"status": "success"}])
    f = F()
    f.upload_file.return_value = {}
    f.create_entity.return_value = {"id": "sc1"}
    out = run(ex.execute_generate_map("p", "S", switch_to_scene=False, app_state=app, foundry=f))
    assert gen.generate_map.await_count == 2 and out["success"] is True
    assert out["background"] == "worlds/maps/map.png"      # upload gave no path: the conventional one is used
    f.set_active_scene.assert_not_called()
    app2, gen2 = _mapgen(tmp_path, [{"status": "error", "provider": "none", "error": "ComfyUI unreachable"}])
    out2 = run(ex.execute_generate_map("p", "S", app_state=app2, foundry=F()))
    assert out2 == {"type": "generate_map", "error": "ComfyUI unreachable"} and gen2.generate_map.await_count == 1
    app3, gen3 = _mapgen(tmp_path, [{"status": "error", "error": "oom"}, {"status": "error", "error": "oom again"}])
    assert run(ex.execute_generate_map("p", "S", app_state=app3, foundry=F()))["error"] == "oom again"


def test_generate_map_generator_exceptions_become_an_error_result(tmp_path):
    app, gen = _mapgen(tmp_path, [])
    gen.generate_map.side_effect = RuntimeError("comfy crashed")
    assert run(ex.execute_generate_map("p", "S", app_state=app, foundry=F())) == {"type": "generate_map", "error": "comfy crashed"}


def test_generate_map_falls_back_to_the_local_path_when_upload_fails(tmp_path):
    app, _ = _mapgen(tmp_path, [{"status": "success"}])
    f = F()
    f.upload_file.side_effect = RuntimeError("upload down")
    f.create_entity.return_value = {}
    out = run(ex.execute_generate_map("p", "S", switch_to_scene=False, app_state=app, foundry=f))
    assert out["background"] == str(tmp_path / "map.png")


def test_generate_map_scene_creation_failure_is_reported_with_the_background(tmp_path):
    app, _ = _mapgen(tmp_path, [{"status": "success"}])
    f = F()
    f.upload_file.return_value = {"path": "worlds/maps/m.png"}
    f.create_entity.side_effect = RuntimeError("scene rejected")
    out = run(ex.execute_generate_map("p", "S", app_state=app, foundry=f))
    assert out == {"type": "generate_map", "error": "scene rejected", "background": "worlds/maps/m.png"}


def test_generate_map_survives_a_failing_narration(tmp_path, monkeypatch):
    async def nosleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", nosleep)
    app, _ = _mapgen(tmp_path, [{"status": "success"}])
    f = F()
    f.upload_file.return_value = {"path": "x"}
    f.create_entity.return_value = {}
    f.chat_message.side_effect = RuntimeError("chat down")
    assert run(ex.execute_generate_map("p", "S", narration="hello", app_state=app, foundry=f))["success"] is True


# ── connection guard ────────────────────────────────────────────────────────

def test_the_connection_guard_names_the_problem():
    ex._require_foundry_connected(F())
    for bad in (None, F(is_connected=False)):
        with pytest.raises(ex.ExecutionError, match="Foundry is not connected"):
            ex._require_foundry_connected(bad)
