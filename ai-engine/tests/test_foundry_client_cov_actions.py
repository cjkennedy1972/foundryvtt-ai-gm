"""FoundryClient scene activation, encounters, sounds, effects, rules helpers and users/rooms."""
import asyncio

import pytest

import foundry.client as client_mod
from test_foundry_client_cov_common import client_with_pw
from foundry import scripts


def run(coro):
    return asyncio.run(coro)


def js(result):
    return {"result": result}


# ── set_active_scene ─────────────────────────────────────────────────────────


def test_set_active_scene_unmatched_scene_falls_back_to_switch_scene(caplog):
    c = client_with_pw({"execute-js": js({"ok": False, "error": "Scene not found", "available": ["A"]}),
                        "switch-scene": {"switched": 1}})
    with caplog.at_level("WARNING"):
        assert run(c.set_active_scene("Nope")) == {"switched": 1}
    assert c._pw.sent[-1] == ("switch-scene", {"name": "Nope"})
    assert any("available=['A']" in r.message for r in caplog.records)


def test_set_active_scene_non_dict_js_result_falls_back(caplog):
    c = client_with_pw({"execute-js": js(None), "switch-scene": {"s": 1}})
    with caplog.at_level("WARNING"):
        assert run(c.set_active_scene("X")) == {"s": 1}
    assert any("available=?" in r.message for r in caplog.records)


def test_set_active_scene_js_error_falls_back_to_switch_scene():
    c = client_with_pw({"execute-js": RuntimeError("js gated"), "switch-scene": {"s": 2}})
    assert run(c.set_active_scene("X")) == {"s": 2}


# ── update_entity / attributes / sound ───────────────────────────────────────


def test_update_entity_omits_empty_data():
    c = client_with_pw({"update": {}})
    run(c.update_entity(token_id="t1"))
    assert c._pw.sent == [("update", {"token_id": "t1"})]


def test_increase_decrease_attribute_use_uuid_param():
    c = client_with_pw()
    run(c.decrease_attribute("system.hp", 3, "Actor.a"))
    run(c.increase_attribute("system.hp", 2, "Actor.a"))
    assert c._pw.sent == [
        ("decrease", {"attribute": "system.hp", "amount": 3, "uuid": "Actor.a"}),
        ("increase", {"attribute": "system.hp", "amount": 2, "uuid": "Actor.a"}),
    ]


def test_play_sound_sends_src_and_volume():
    c = client_with_pw()
    run(c.play_sound("sounds/x.ogg", volume=0.2))
    assert c._pw.sent == [("play-sound", {"src": "sounds/x.ogg", "volume": 0.2})]


# ── playlists ────────────────────────────────────────────────────────────────


def test_play_playlist_applies_volume_only_to_playing_sounds():
    reply = {"data": {"playlist": {"sounds": [
        {"id": "s1", "playing": True, "name": "A"},
        {"id": "s2", "playing": False, "name": "B"},
        {"id": None, "playing": True, "name": "C"},
    ]}}}
    c = client_with_pw({"playlist-play": reply})
    assert run(c.play_playlist("Tavern", volume=0.3)) == reply
    assert c._pw.sent == [
        ("playlist-play", {"playlistName": "Tavern"}),
        ("playlist-volume", {"playlistName": "Tavern", "soundId": "s1", "volume": 0.3}),
    ]


def test_play_playlist_warns_when_no_sound_reported(caplog):
    c = client_with_pw({"playlist-play": {}})
    with caplog.at_level("WARNING"):
        run(c.play_playlist("Tavern"))
    assert [m for m, _ in c._pw.sent] == ["playlist-play"]
    assert any("no playing sound" in r.message for r in caplog.records)


def test_play_playlist_volume_failure_is_logged_not_raised(caplog):
    c = client_with_pw({"playlist-play": {"data": {"playlist": {"sounds": [{"id": "s1", "playing": True, "name": "A"}]}}},
                        "playlist-volume": RuntimeError("nope")})
    with caplog.at_level("WARNING"):
        assert run(c.play_playlist("T")) is not None
    assert any("volume was not applied to 'A'" in r.message for r in caplog.records)


# ── encounters ───────────────────────────────────────────────────────────────


def test_start_encounter_relay_without_tokens_sends_empty_combat():
    c = client_with_pw({"start-encounter": {"ok": 1}})
    assert run(c.start_encounter(roll_all=True, name="Ambush")) == {"ok": 1}
    assert c._pw.sent == [("start-encounter", {"tokens": [], "rollAll": True, "name": "Ambush"})]


def test_start_encounter_selects_tokens_then_starts_with_selected():
    c = client_with_pw({"execute-js": js(2), "start-encounter": {"ok": 1}})
    run(c.start_encounter(tokens=["t1", "t2"]))
    assert c._pw.sent[0][0] == "execute-js"
    assert 'const ids=["t1", "t2"];' in c._pw.sent[0][1]["script"]
    assert c._pw.sent[1] == ("start-encounter", {"startWithSelected": True, "rollAll": False})


@pytest.mark.parametrize("reply", [js(0), RuntimeError("js gated"), {"x": 1}])
def test_start_encounter_warns_but_still_starts_when_no_token_resolves(reply, caplog):
    c = client_with_pw({"execute-js": reply, "start-encounter": {"ok": 1}})
    with caplog.at_level("WARNING"):
        assert run(c.start_encounter(tokens=["t1"])) == {"ok": 1}
    assert any("none of 1 token id(s) resolved" in r.message for r in caplog.records)
    assert c._pw.sent[-1][1]["startWithSelected"] is True


# ── dnd5e helpers ────────────────────────────────────────────────────────────

def test_use_spell_slot_returns_result_dict_or_empty():
    c = client_with_pw({"execute-js": js({"ok": True, "remaining": 1})})
    assert run(c.use_spell_slot("Actor.a", 2)) == {"ok": True, "remaining": 1}
    assert c._pw.sent[0][1]["script"] == scripts.spend_spell_slot("Actor.a", 2)
    assert run(client_with_pw({"execute-js": js(None)}).use_spell_slot("Actor.a", 2)) == {}
    assert run(client_with_pw({"execute-js": []}).use_spell_slot("Actor.a", 2)) == {}


@pytest.mark.parametrize("given,code", [("Animal_Handling", "ani"), (" sleight-of-hand ", "slt"),
                                         ("Perception", "prc"), ("custom skill", "custom skill")])
def test_skill_check_normalises_names(given, code):
    c = client_with_pw()
    run(c.request_skill_check("Actor.a", given, dc=12))
    assert c._pw.sent == [("skill-check", {"actorUuid": "Actor.a", "skill": code})]


def test_skill_check_none_skill_passes_through():
    c = client_with_pw()
    run(c.request_skill_check("Actor.a", None))
    assert c._pw.sent[0][1]["skill"] is None


@pytest.mark.parametrize("adv,extra", [(True, {"advantage": True}), (False, {"disadvantage": True}), (None, {})])
def test_saving_throw_and_death_save_advantage_flags(adv, extra):
    c = client_with_pw()
    run(c.request_saving_throw("Actor.a", " Dexterity ", advantage=adv))
    run(c.request_death_save("Actor.a", advantage=adv))
    assert c._pw.sent == [
        ("ability-save", {"actorUuid": "Actor.a", "ability": "dex", **extra}),
        ("death-save", {"actorUuid": "Actor.a", **extra}),
    ]


def test_saving_throw_unknown_ability_passes_through():
    c = client_with_pw()
    run(c.request_saving_throw("Actor.a", "luck"))
    assert c._pw.sent[0][1]["ability"] == "luck"


def test_rests_and_concentration_messages():
    c = client_with_pw()
    run(c.request_short_rest("Actor.a"))
    run(c.request_short_rest("Actor.a", auto_hd=False))
    run(c.request_long_rest("Actor.a"))
    run(c.request_long_rest("Actor.a", new_day=False))
    run(c.break_concentration("Actor.a"))
    assert c._pw.sent == [
        ("short-rest", {"actorUuid": "Actor.a", "autoHD": True}),
        ("short-rest", {"actorUuid": "Actor.a", "autoHD": False}),
        ("long-rest", {"actorUuid": "Actor.a", "newDay": True}),
        ("long-rest", {"actorUuid": "Actor.a", "newDay": False}),
        ("break-concentration", {"actorUuid": "Actor.a"}),
    ]


def test_solo_death_setback_applies_exhaustion_after_ok_script():
    c = client_with_pw({"execute-js": js({"ok": True, "hp": 1}), "add-effect": {}})
    assert run(c.apply_solo_death_setback("Actor.a")) == {"ok": True, "hp": 1, "exhaustion": True}
    assert c._pw.sent[1] == ("add-effect", {"uuid": "Actor.a", "statusId": "exhaustion"})


@pytest.mark.parametrize("payload,msg", [(js({"ok": False, "error": "dead for good"}), "dead for good"),
                                          (js(None), "solo death setback failed"),
                                          (js({"ok": False}), "solo death setback failed")])
def test_solo_death_setback_raises_and_applies_nothing_when_script_not_ok(payload, msg):
    c = client_with_pw({"execute-js": payload})
    with pytest.raises(RuntimeError, match=msg):
        run(c.apply_solo_death_setback("Actor.a"))
    assert [m for m, _ in c._pw.sent] == ["execute-js"]


def test_apply_condition_lowercases_and_delegates_to_add_effect():
    c = client_with_pw()
    run(c.apply_condition("Actor.a", "PRONE", duration="1 round"))
    assert c._pw.sent == [("add-effect", {"uuid": "Actor.a", "statusId": "prone", "duration": "1 round"})]


def test_add_effect_omits_duration_when_not_given():
    c = client_with_pw()
    run(c.add_effect("Actor.a", "prone"))
    assert c._pw.sent == [("add-effect", {"uuid": "Actor.a", "statusId": "prone"})]


def test_script_backed_reads_unwrap_result_and_degrade_to_documented_defaults():
    ok = {"execute-js": js({"v": 1})}
    bad = {"execute-js": RuntimeError("x")}
    odd = {"execute-js": []}
    cases = [
        (lambda c: c.get_passive_perception("a"), {"v": 1}, {"passivePerception": 10}, {"passivePerception": 10}),
        (lambda c: c.contested_check("a", "b"), {"v": 1}, {"error": "x"}, {"error": "No result"}),
        (lambda c: c.check_spell_ritual("a", "s"), {"v": 1}, {"isRitual": False}, {"isRitual": False}),
        (lambda c: c.get_spell_slots("a"), {"v": 1}, {}, {}),
        (lambda c: c.get_multiattack_count("a"), {"v": 1}, {"count": 1, "description": ""},
         {"count": 1, "description": ""}),
        (lambda c: c.get_legendary_resistance("a"), {"v": 1}, {"value": 0, "max": 0}, {"value": 0, "max": 0}),
        (lambda c: c.spend_legendary_resistance("a"), {"v": 1}, {"ok": False, "used": False},
         {"ok": False, "used": False}),
    ]
    for call, good, on_error, on_odd in cases:
        assert run(call(client_with_pw(ok))) == good
        assert run(call(client_with_pw(bad))) == on_error
        assert run(call(client_with_pw(odd))) == on_odd


def test_contested_check_forwards_abilities_to_script():
    c = client_with_pw({"execute-js": js({"w": 1})})
    run(c.contested_check("a", "b", "dex", "con"))
    assert c._pw.sent[0][1]["script"] == scripts.contested_check("a", "b", "dex", "con")


# ── users / rooms ────────────────────────────────────────────────────────────


def test_get_rooms_shapes():
    assert run(client_with_pw({"structure": {"rooms": ["a"]}}).get_rooms()) == ["a"]
    assert run(client_with_pw({"structure": {"data": {"rooms": ["b"]}}}).get_rooms()) == ["b"]
    assert run(client_with_pw({"structure": {"rooms": "x"}}).get_rooms()) == []
    assert run(client_with_pw({"structure": RuntimeError("x")}).get_rooms()) == []


def test_timeouts_constant_wiring():
    # canvas switches use the longer timeout the settings validate as >= the data timeout
    assert client_mod.settings.relay_rpc_timeout_canvas >= client_mod.settings.relay_rpc_timeout
