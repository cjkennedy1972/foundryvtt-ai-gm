"""Behavioural coverage for the remaining api/routes/immersion.py endpoints.

Uses the real managers so each assertion is about state that changed, not a
mock echoing its arguments.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import AppState, get_app_state
from api.routes import immersion as immersion_routes
from immersion.ambient import AmbientManager
from immersion.items import ItemManager
from immersion.macros import MacroManager
from immersion.particles import ParticleManager


@pytest.fixture
def state():
    s = AppState()
    s.ambient_manager = AmbientManager()
    s.macro_manager = MacroManager()
    s.particle_manager = ParticleManager()
    s.item_manager = ItemManager()
    s.action_dispatcher = AsyncMock()
    return s


def _client(state):
    app = FastAPI()
    app.include_router(immersion_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


class TestUninitialisedManagers:
    """Each endpoint reports its missing manager instead of crashing."""

    @pytest.mark.parametrize("method,path,params", [
        ("post", "/weather", {"weather": "rain"}),
        ("post", "/time", {"time": "dawn"}),
        ("get", "/atmosphere", None),
        ("post", "/token-effect", {"token_id": "t", "effect_type": "c", "effect_name": "n"}),
        ("get", "/token-effects/t", None),
        ("post", "/vision", {"token_id": "t", "vision_range": 30}),
        ("get", "/vision-status", None),
        ("get", "/macros", None),
        ("get", "/macro-templates", None),
        ("post", "/particle-preset", {"effect_id": "e", "preset_name": "p", "x": 0, "y": 0}),
        ("get", "/particles", None),
        ("get", "/particle-presets", None),
        ("get", "/item-pools", None),
        ("get", "/inventory/a", None),
    ])
    def test_reports_not_initialised(self, method, path, params):
        r = getattr(_client(AppState()), method)("/api/immersion" + path, params=params)
        assert r.status_code == 200
        assert "not initialized" in r.json()["error"]


class TestWeatherAndTime:
    def test_weather_is_case_insensitive_and_applied(self, state):
        r = _client(state).post("/api/immersion/weather", params={"weather": "RAIN"})
        assert "error" not in r.json()
        assert "rain" in str(state.ambient_manager.__dict__).lower()

    def test_unknown_weather_and_time_leave_state_alone(self, state):
        before = repr(state.ambient_manager.__dict__)
        c = _client(state)
        assert c.post("/api/immersion/weather", params={"weather": "lava"}).json() == {
            "error": "Unknown weather type: lava"}
        assert c.post("/api/immersion/time", params={"time": "teatime"}).json() == {
            "error": "Unknown time: teatime"}
        assert repr(state.ambient_manager.__dict__) == before

    def test_time_valid_and_atmosphere_reflects_it(self, state):
        c = _client(state)
        assert "error" not in c.post("/api/immersion/time", params={"time": "NIGHT"}).json()
        atm = c.get("/api/immersion/atmosphere").json()
        assert atm["description"] and "modifiers" in atm


class TestEffects:
    def test_token_effects_listed_for_token(self):
        from immersion.effects import EffectsManager
        s = AppState()
        s.effects_manager = EffectsManager()
        r = _client(s).get("/api/immersion/token-effects/tok9")
        assert r.json()["token_id"] == "tok9" and "effects" in r.json()


class TestMacros:
    REG = {"macro_id": "m1", "name": "Torch", "description": "light", "action_type": "play_sound"}

    def test_register_list_execute_roundtrip(self, state):
        c = _client(state)
        state.action_dispatcher.execute.return_value = {"ok": True}
        r = c.post("/api/immersion/macro/register", params=self.REG, json={"sound": "a.mp3"})
        assert r.status_code == 200, r.text
        assert [m["id"] for m in c.get("/api/immersion/macros").json()["macros"]] == ["m1"]

        out = c.post("/api/immersion/macro/execute", params={"macro_id": "m1"},
                     json={"sound": "b.mp3"}).json()
        assert out["action_type"] == "play_sound" and out["result"] == {"ok": True}
        # override wins over the registered parameter, and `type` is the macro's action
        state.action_dispatcher.execute.assert_awaited_once_with({"type": "play_sound", "sound": "b.mp3"})

    def test_register_requires_parameters_body(self, state):
        assert _client(state).post("/api/immersion/macro/register", params=self.REG).status_code == 422

    def test_execute_unknown_macro_does_not_dispatch(self, state):
        r = _client(state).post("/api/immersion/macro/execute", params={"macro_id": "zzz"})
        assert "not found" in r.json()["error"].lower()
        state.action_dispatcher.execute.assert_not_awaited()

    def test_execute_refuses_recursive_macro(self, state):
        c = _client(state)
        c.post("/api/immersion/macro/register", params={**self.REG, "action_type": "execute_macro"}, json={})
        r = c.post("/api/immersion/macro/execute", params={"macro_id": "m1"})
        assert "cannot invoke execute_macro" in r.json()["error"]
        state.action_dispatcher.execute.assert_not_awaited()

    def test_execute_needs_manager_and_dispatcher(self, state):
        state.action_dispatcher = None
        r = _client(state).post("/api/immersion/macro/execute", params={"macro_id": "m1"})
        assert r.json() == {"error": "Action dispatcher not initialized"}
        state.macro_manager = None
        r = _client(state).post("/api/immersion/macro/execute", params={"macro_id": "m1"})
        assert r.json() == {"error": "Macro manager not initialized"}

    def test_register_without_manager(self):
        r = _client(AppState()).post("/api/immersion/macro/register",
                                     params=TestMacros.REG, json={})
        assert r.json() == {"error": "Macro manager not initialized"}

    def test_templates_exposed(self, state):
        assert _client(state).get("/api/immersion/macro-templates").json()["templates"]


class TestParticles:
    def test_create_then_list_and_count(self, state):
        c = _client(state)
        r = c.post("/api/immersion/particle", params={
            "effect_id": "e1", "name": "Spark", "effect_type": "fire", "x": 3, "y": 4, "color": "#f00"})
        assert r.json()["position"] == [3.0, 4.0] and r.json()["visual"]["color"] == "#f00"
        got = c.get("/api/immersion/particles").json()
        assert got["count"] == 1 and "e1" in got["active_effects"]

    def test_particle_requires_position(self, state):
        r = _client(state).post("/api/immersion/particle", params={
            "effect_id": "e", "name": "n", "effect_type": "t"})
        assert r.status_code == 422 and state.particle_manager.get_effect_count() == 0

    def test_particle_without_manager(self):
        r = _client(AppState()).post("/api/immersion/particle", params={
            "effect_id": "e", "name": "n", "effect_type": "t", "x": 0, "y": 0})
        assert r.json() == {"error": "Particle manager not initialized"}

    def test_preset_known_and_unknown(self, state):
        c = _client(state)
        preset = next(iter(state.particle_manager.list_presets()))
        ok = c.post("/api/immersion/particle-preset",
                    params={"effect_id": "p1", "preset_name": preset, "x": 1, "y": 2}).json()
        assert ok["name"] == preset and state.particle_manager.get_effect_count() == 1
        bad = c.post("/api/immersion/particle-preset",
                     params={"effect_id": "p2", "preset_name": "nope", "x": 1, "y": 2}).json()
        assert "Unknown particle preset" in bad["error"] and state.particle_manager.get_effect_count() == 1
        assert preset in c.get("/api/immersion/particle-presets").json()["presets"]


class TestLoot:
    ITEM = {"pool_name": "crypt", "item_id": "sw1", "name": "Sword", "rarity": "rare",
            "value_gp": 500, "weight_lbs": 3, "description": "sharp"}

    def test_add_item_lists_pool(self, state):
        c = _client(state)
        r = c.post("/api/immersion/item-pool", params=self.ITEM)
        assert r.json()["item_name"] == "Sword"
        assert c.get("/api/immersion/item-pools").json()["pools"] == {"crypt": 1}
        assert state.item_manager.available_items["sw1"].quantity == 1

    def test_add_item_validates_numbers(self, state):
        r = _client(state).post("/api/immersion/item-pool", params={**self.ITEM, "value_gp": "lots"})
        assert r.status_code == 422 and state.item_manager.loot_pools == {}

    def test_add_item_without_manager(self):
        assert _client(AppState()).post("/api/immersion/item-pool", params=self.ITEM).json() == {
            "error": "Item manager not initialized"}

    def test_inventory_totals_distributed_items(self, state):
        c = _client(state)
        c.post("/api/immersion/item-pool", params=self.ITEM)
        state.item_manager.distribute_item("sw1", "hero")
        inv = c.get("/api/immersion/inventory/hero").json()
        assert inv["item_count"] == 1 and inv["total_value_gp"] == 500
        assert c.get("/api/immersion/inventory/nobody").json()["item_count"] == 0
