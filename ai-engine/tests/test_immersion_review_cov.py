"""Ambient, macros, effects, cinema, storyteller-module cache: behaviours the existing suite leaves out."""
import asyncio
import json


from immersion import storyteller
from immersion.ambient import AmbientManager, TimeOfDay, WeatherType
from immersion.cinema import CinemaDirector
from immersion.effects import EffectsManager
from immersion.macros import MacroManager


# ── ambient ─────────────────────────────────────────────────────────────────

def test_weather_and_time_changes_report_their_effects_and_update_state():
    a = AmbientManager()
    w = a.set_weather(WeatherType.THUNDERSTORM)
    assert a.current_weather is WeatherType.THUNDERSTORM and w["type"] == "weather_changed" and w["weather"] == "thunderstorm"
    assert w["effects"]["visibility_modifier"] == AmbientManager.WEATHER_EFFECTS[WeatherType.THUNDERSTORM]["visibility_bonus"]
    t = a.set_time(TimeOfDay.NIGHT)
    assert a.current_time is TimeOfDay.NIGHT and t["effects"]["need_light_sources"] is True and t["effects"]["lighting"] == "dark"
    assert a.set_time(TimeOfDay.NOON)["effects"]["need_light_sources"] is False


def test_atmosphere_description_joins_time_weather_and_noticed_effects():
    a = AmbientManager()
    a.set_weather(WeatherType.FOG)
    a.set_time(TimeOfDay.DUSK)
    assert a.get_atmosphere_description() == (
        f"{AmbientManager.TIME_ATMOSPHERE[TimeOfDay.DUSK]['description']} {AmbientManager.WEATHER_EFFECTS[WeatherType.FOG]['description']}")
    added = a.add_atmospheric_effect("a distant bell")
    a.add_atmospheric_effect("the smell of smoke")
    assert added == {"type": "atmospheric_effect_added", "effect": "a distant bell", "active_effects": ["a distant bell", "the smell of smoke"]}
    assert a.get_atmosphere_description().endswith(" You notice a distant bell, the smell of smoke.")


def test_environmental_modifiers_reflect_current_state_and_default_for_unlisted_values():
    a = AmbientManager()
    a.set_time(TimeOfDay.EVENING)
    a.add_atmospheric_effect("mist")
    m = a.get_environmental_modifiers()
    assert m["weather"] == "clear" and m["time"] == "evening" and m["darkness_enabled"] is True and m["active_effects"] == ["mist"]
    a.current_weather = WeatherType.TORNADO           # no WEATHER_EFFECTS entry: must not raise
    assert a.get_environmental_modifiers()["visibility_modifier"] == 0
    assert a.set_weather(WeatherType.TORNADO)["description"] == ""


# ── macros ──────────────────────────────────────────────────────────────────

def _macros():
    m = MacroManager()
    m.register_macro("rain", "Storm", "d", "set_weather", {"weather": "rain", "intensity": 1})
    return m


def test_a_resolved_macro_is_an_action_dict_with_overrides_winning():
    m = _macros()
    assert m.resolve_macro("rain", {"intensity": 5}) == {"type": "set_weather", "weather": "rain", "intensity": 5}
    assert m.registered_macros["rain"]["parameters"]["intensity"] == 1      # the stored macro is not mutated
    assert m.get_macro_history()[0]["parameters"]["intensity"] == 5


def test_unknown_macros_and_recursive_macros_are_refused_without_history():
    m = _macros()
    m.register_macro("loop", "Loop", "d", "execute_macro", {"macro_id": "loop"})
    assert m.resolve_macro("nope") == {"error": "Macro not found: nope"}
    assert m.resolve_macro("loop") == {"error": "A macro cannot invoke execute_macro"}
    assert m.get_macro_history() == []


def test_history_is_capped_and_limited():
    m = _macros()
    m.max_history = 3
    for i in range(5):
        m.resolve_macro("rain", {"intensity": i})
    assert [h["parameters"]["intensity"] for h in m.macro_history] == [2, 3, 4]
    assert [h["parameters"]["intensity"] for h in m.get_macro_history(limit=2)] == [3, 4]


def test_list_and_delete_macros():
    m = _macros()
    assert m.list_macros() == [{"id": "rain", "name": "Storm", "description": "d", "action_type": "set_weather"}]
    assert m.delete_macro("rain") == {"type": "macro_deleted", "macro_id": "rain", "name": "Storm"}
    assert m.list_macros() == [] and m.delete_macro("rain") == {"error": "Macro not found: rain"}


def test_every_template_resolves_to_a_real_action_without_recursion():
    m = MacroManager()
    for tid, t in m.get_macro_templates().items():
        assert t["action_type"] != "execute_macro"
        m.register_macro(tid, t["name"], t["description"], t["action_type"], t["parameters"])
        assert m.resolve_macro(tid)["type"] == t["action_type"]


# ── effects ─────────────────────────────────────────────────────────────────

def test_effects_accumulate_per_token_and_report_their_visuals():
    e = EffectsManager()
    e.apply_condition_visual("t1", "Blinded", duration=3)
    e.apply_aura("t1", "no-such-aura")                 # unknown aura gets a default look
    got = e.get_token_effects("t1")
    assert [(x["type"], x["name"], x["duration"]) for x in got] == [("status", "Blinded", 3), ("aura", "no-such-aura", None)]
    assert got[1]["description"] == "Aura: no-such-aura" and got[1]["color"] == "#ffffff"
    assert e.get_token_effects("other") == []


def test_removing_an_effect_drops_only_matching_ones_and_reports_the_rest():
    e = EffectsManager()
    e.apply_condition_visual("t1", "Blinded")
    e.apply_condition_visual("t1", "Prone")
    assert e.remove_effect("t1", "Blinded") == {"type": "effect_removed", "token_id": "t1", "removed_effect": "Blinded", "remaining_effects": 1}
    assert [x["name"] for x in e.get_token_effects("t1")] == ["Prone"]
    assert e.remove_effect("ghost", "Blinded") == {"error": "No effects found for ghost"}


# ── cinema ──────────────────────────────────────────────────────────────────

class FakeFoundry:
    def __init__(self, result=True, raises=False):
        self.scripts, self.result, self.raises = [], result, raises

    async def execute_js(self, script):
        self.scripts.append(script)
        if self.raises:
            raise RuntimeError("relay down")
        return {"result": self.result(script) if callable(self.result) else self.result}


def run(c):
    return asyncio.run(c)


def test_cinema_is_a_noop_when_the_module_is_unavailable():
    f = FakeFoundry(result=False)
    c = CinemaDirector(f)
    assert run(c.clear_subtitles()) is False and run(c.scene_flags()) is None
    assert run(c.set_scene(active=True)) is False and run(c.restore_scene(None, {"active": True})) is False
    assert not any("setFlag" in x or "clearSubtitles" in x or "unsetFlag" in x for x in f.scripts)   # nothing was written


def test_cinema_survives_a_dead_relay():
    c = CinemaDirector(FakeFoundry(raises=True))
    assert run(c.available()) is False and run(c.say("GM", "hi")) is False


def test_clear_subtitles_runs_the_clear_call():
    f = FakeFoundry()
    assert run(CinemaDirector(f).clear_subtitles()) is True
    assert f.scripts[-1] == "window.StorytellerCinema.clearSubtitles(); return true;"


def test_scene_flags_returns_a_dict_or_none_and_targets_the_right_scene():
    f = FakeFoundry(result=lambda s: {"active": True} if "getFlag" in s else True)
    c = CinemaDirector(f)
    assert run(c.scene_flags()) == {"active": True}
    assert "game.scenes.active" in f.scripts[-1]
    run(c.scene_flags('Cr"ypt'))
    assert '(game.scenes.get("Cr\\"ypt") || game.scenes.getName("Cr\\"ypt"))' in f.scripts[-1]   # name is JSON-escaped, not spliced raw
    f2 = FakeFoundry(result=lambda s: None if "getFlag" in s else True)
    assert run(CinemaDirector(f2).scene_flags()) is None


def test_set_scene_sends_only_the_flags_given_and_skips_when_nothing_to_set():
    f = FakeFoundry()
    c = CinemaDirector(f)
    assert run(c.set_scene()) is False and f.scripts == []                  # nothing to set: not even a probe
    assert run(c.set_scene("s1", active=True, dim=0.0, background=None)) is True
    sent = f.scripts[-1]
    assert json.dumps({"active": True, "cinematicBgDim": 0.0}) in sent      # 0.0 is a value, not "unset"


def test_restore_scene_unsets_flags_that_were_absent_and_resets_the_rest():
    f = FakeFoundry()
    c = CinemaDirector(f)
    assert run(c.restore_scene("s1", {"active": None, "viewMode": "cinematic"})) is True
    s = f.scripts[-1]
    assert "unsetFlag" in s and '"viewMode": "cinematic"' in s


# ── storyteller module cache ────────────────────────────────────────────────

class ModsFoundry:
    def __init__(self, info):
        self.info, self.calls = info, 0

    async def get_active_modules_info(self):
        self.calls += 1
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


def test_active_module_ids_are_cached_for_a_minute_then_refreshed(monkeypatch):
    f = ModsFoundry({"modules": [{"id": "a"}, "b"]})
    now = [100.0]
    monkeypatch.setattr(storyteller.time, "monotonic", lambda: now[0])
    assert run(storyteller.active_module_ids(f)) == {"a", "b"}
    now[0] += 30
    f.info = {"modules": []}
    assert run(storyteller.active_module_ids(f)) == {"a", "b"} and f.calls == 1
    now[0] += 31
    assert run(storyteller.active_module_ids(f)) == set() and f.calls == 2


def test_an_unreadable_module_list_is_empty_and_not_cached():
    f = ModsFoundry(RuntimeError("boom"))
    assert run(storyteller.active_module_ids(f)) == set()
    f.info = {"modules": [{"id": "x"}]}
    assert run(storyteller.active_module_ids(f)) == {"x"}
