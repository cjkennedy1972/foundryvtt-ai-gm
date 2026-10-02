"""Behavioural coverage for api/routes/{combat,control,scene,npc}.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import combat as combat_routes
from api.routes import control as control_routes
from api.routes import npc as npc_routes
from api.routes import scene as scene_routes


def _client(state, *routers):
    app = FastAPI()
    for r in routers:
        app.include_router(r.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _h(request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump(),
        )

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def state():
    s = AppState()
    s.foundry_client = AsyncMock()
    s.foundry_client.is_connected = True
    s.state_tracker = MagicMock()
    return s


# ---------------------------------------------------------------- combat

class TestCombatStart:
    def _c(self, state):
        return _client(state, combat_routes)

    def test_no_loop_is_503(self, state):
        state.combat_loop = None
        r = self._c(state).post("/api/combat/start")
        assert r.status_code == 503 and r.json()["code"] == "COMBAT_NOT_READY"

    def test_no_foundry_is_503(self, state):
        state.combat_loop = AsyncMock()
        state.foundry_client = None
        r = self._c(state).post("/api/combat/start")
        assert r.status_code == 503 and r.json()["code"] == "FOUNDRY_NOT_CONNECTED"

    def test_empty_scene_is_400_and_loop_not_started(self, state):
        state.combat_loop = AsyncMock()
        state.foundry_client.get_scene_tokens.return_value = []
        r = self._c(state).post("/api/combat/start")
        assert r.status_code == 400 and r.json()["code"] == "NO_TOKENS_FOUND"
        state.combat_loop.start_combat_loop.assert_not_awaited()

    def test_starts_loop_with_scene_tokens(self, state):
        state.combat_loop = AsyncMock()
        toks = [{"id": "a"}, {"id": "b"}]
        state.foundry_client.get_scene_tokens.return_value = toks
        r = self._c(state).post("/api/combat/start")
        assert r.json() == {"status": "started", "tokens": 2}
        state.combat_loop.start_combat_loop.assert_awaited_once_with(toks)

    def test_loop_failure_does_not_leak_message(self, state):
        state.combat_loop = AsyncMock()
        state.foundry_client.get_scene_tokens.return_value = [{"id": "a"}]
        state.combat_loop.start_combat_loop.side_effect = RuntimeError("/secret/path")
        r = self._c(state).post("/api/combat/start")
        assert r.status_code == 500 and r.json()["code"] == "COMBAT_START_FAILED"
        assert "secret" not in r.text


class TestCombatMisc:
    def test_stop_without_loop_reports_not_running(self, state):
        state.combat_loop = None
        assert _client(state, combat_routes).post("/api/combat/stop").json() == {"status": "not running"}

    def test_status_without_loop(self, state):
        state.combat_loop = None
        assert _client(state, combat_routes).get("/api/combat/status").json() == {"running": False}

    def test_snapshot_none_and_present(self, state):
        c = _client(state, combat_routes)
        state.state_tracker.get_combat_snapshot.return_value = None
        assert c.get("/api/combat/snapshot").json()["snapshot"] is None
        state.state_tracker.get_combat_snapshot.return_value = {"hp": 3}
        assert c.get("/api/combat/snapshot").json() == {"snapshot": {"hp": 3}}

    def test_tactical_analyze_without_enemies_says_so(self, state):
        with patch("actions.executors._resolve_token_id", AsyncMock(return_value="t1")), \
             patch("combat.tactics.build_tactical_snapshot", AsyncMock(return_value="")):
            r = _client(state, combat_routes).post("/api/combat/tactical/analyze", params={"actor_id": "bob"})
        assert r.json() == {"actor": "bob", "analysis": "No enemies visible on the current scene."}

    def test_tactical_analyze_returns_snapshot(self, state):
        with patch("actions.executors._resolve_token_id", AsyncMock(return_value="t1")), \
             patch("combat.tactics.build_tactical_snapshot", AsyncMock(return_value="orc 10ft")):
            r = _client(state, combat_routes).post("/api/combat/tactical/analyze", params={"actor_id": "bob"})
        assert r.json()["analysis"] == "orc 10ft"

    def test_tactical_requires_foundry(self, state):
        state.foundry_client.is_connected = False
        r = _client(state, combat_routes).post("/api/combat/tactical/analyze", params={"actor_id": "bob"})
        assert r.status_code == 503

    def test_flanking_true_and_false_benefits(self, state):
        c = _client(state, combat_routes)
        for flag, benefit in ((True, "Gain advantage on attack roll"), (False, "No flanking benefit")):
            with patch("combat.tactics.flanking_check", MagicMock(return_value=flag)), \
                 patch("combat.tactics.fetch_scene_state", AsyncMock(return_value={"tokens": []})), \
                 patch("actions.executors._resolve_token_id", AsyncMock(side_effect=["a", "b"])):
                r = c.post("/api/combat/tactical/flanking", params={"attacker_id": "x", "target_id": "y"})
            assert r.json()["is_flanking"] is flag and r.json()["benefit"] == benefit

    def test_difficulty_suggest_rejects_missing_params(self, state):
        r = _client(state, combat_routes).post("/api/combat/difficulty/suggest", params={"num_players": 4})
        assert r.status_code == 422

    def test_difficulty_suggest_scales_with_monsters(self, state):
        c = _client(state, combat_routes)
        easy = c.post("/api/combat/difficulty/suggest",
                      params={"num_players": 4, "avg_level": 5}, json=[0.25]).json()
        hard = c.post("/api/combat/difficulty/suggest",
                      params={"num_players": 4, "avg_level": 5}, json=[8, 8, 8]).json()
        assert hard["estimated_xp"] > easy["estimated_xp"]
        assert hard["difficulty"] != easy["difficulty"]

    def test_suggestions_valid_band_and_unknown_band(self, state):
        c = _client(state, combat_routes)
        ok = c.get("/api/combat/difficulty/suggestions",
                   params={"num_players": 4, "avg_level": 5, "difficulty": " hard "})
        assert ok.status_code == 200 and ok.json()["party_level"] == 5
        bad = c.get("/api/combat/difficulty/suggestions",
                    params={"num_players": 4, "avg_level": 5, "difficulty": "nope"})
        assert bad.status_code == 400 and bad.json()["code"] == "UNKNOWN_DIFFICULTY"


class TestSpatialRelationships:
    SCENE = {
        "grid": 100,
        "tokens": [
            {"id": "pc", "name": "Hero", "x": 0, "y": 0, "disposition": 1},
            {"id": "far", "name": "Orc", "x": 1000, "y": 0, "disposition": -1},
            {"id": "near", "name": "Rat", "x": 200, "y": 0, "disposition": -1},
            {"id": "ghost", "name": "Secret", "x": 100, "y": 0, "disposition": -1, "hidden": True},
        ],
        # vertical wall between pc (50,50) and "far" (1050,50)
        "walls": [{"c": [500, -100, 500, 300]}],
    }

    def _get(self, state, scene):
        with patch("combat.tactics.fetch_scene_state", AsyncMock(return_value=scene)):
            return _client(state, combat_routes).get("/api/combat/spatial-relationships")

    def test_sorted_by_distance_with_cover_direction_and_hidden_excluded(self, state):
        body = self._get(state, self.SCENE).json()
        assert body["pc_token"] == "pc"
        ids = [r["id"] for r in body["relationships"]]
        assert ids == ["near", "far"]  # hidden token never leaks, nearest first
        near, far = body["relationships"]
        assert near["cover"] == "none" and near["direction"] == "center"
        assert far["cover"] == "half" and far["direction"] == "right"
        assert near["distance_ft"] < far["distance_ft"]

    def test_direction_left(self, state):
        scene = {"grid": 100, "tokens": [
            {"id": "pc", "x": 1000, "y": 0, "disposition": 1},
            {"id": "e", "name": "E", "x": 0, "y": 0, "disposition": -1}]}
        assert self._get(state, scene).json()["relationships"][0]["direction"] == "left"

    def test_no_pc_token(self, state):
        scene = {"grid": 100, "tokens": [{"id": "e", "x": 0, "y": 0, "disposition": -1}]}
        assert self._get(state, scene).json()["error"] == "No PC token found"

    def test_unusable_scene_state(self, state):
        assert self._get(state, "garbage").json()["error"] == "Could not analyze scene"

    def test_internal_failure_is_500_without_message(self, state):
        with patch("combat.tactics.fetch_scene_state", AsyncMock(return_value=self.SCENE)), \
             patch("combat.tactics._build_mechanics", side_effect=ValueError("/etc/passwd")):
            r = _client(state, combat_routes).get("/api/combat/spatial-relationships")
        assert r.status_code == 500 and "passwd" not in r.text


# --------------------------------------------------------------- control

class TestControl:
    def _state(self, state):
        state.chat_listener = MagicMock()
        state.chat_listener._running = True
        return state

    def test_pause_and_resume_without_listener_are_503(self, state):
        state.chat_listener = None
        c = _client(state, control_routes)
        assert c.post("/api/admin/pause").status_code == 503
        assert c.post("/api/admin/resume").status_code == 503

    def test_pause_stops_listener_pauses_game_and_broadcasts(self, state):
        self._state(state)
        with patch("api.routes.control.broadcast_state_update", AsyncMock()) as bc:
            r = _client(state, control_routes).post("/api/admin/pause")
        assert r.json() == {"status": "paused", "ai_running": False}
        assert state.chat_listener._running is False
        assert "togglePause(true" in state.foundry_client.execute_js.await_args.args[0]
        bc.assert_awaited_once_with({"type": "ai_paused"})

    def test_pause_survives_foundry_failure(self, state):
        self._state(state)
        state.foundry_client.execute_js.side_effect = RuntimeError("down")
        with patch("api.routes.control.broadcast_state_update", AsyncMock()):
            r = _client(state, control_routes).post("/api/admin/pause")
        assert r.status_code == 200 and state.chat_listener._running is False

    def test_resume_restarts_listener_and_resets_idle_timer(self, state):
        self._state(state)
        state.chat_listener._running = False
        with patch("api.routes.control.broadcast_state_update", AsyncMock()) as bc:
            r = _client(state, control_routes).post("/api/admin/resume")
        assert r.json() == {"status": "resumed", "ai_running": True}
        assert state.chat_listener._running is True
        state.chat_listener._reset_idle_timer.assert_called_once()
        assert "togglePause(false" in state.foundry_client.execute_js.await_args.args[0]
        bc.assert_awaited_once_with({"type": "ai_resumed"})

    def test_resume_survives_foundry_failure(self, state):
        self._state(state)
        state.foundry_client.execute_js.side_effect = RuntimeError("down")
        with patch("api.routes.control.broadcast_state_update", AsyncMock()):
            assert _client(state, control_routes).post("/api/admin/resume").status_code == 200

    @pytest.mark.parametrize("text", ["", "   \n"])
    def test_narrate_rejects_blank(self, state, text):
        r = _client(state, control_routes).post("/api/admin/narrate", json={"text": text})
        assert r.status_code == 400
        state.foundry_client.chat_message.assert_not_awaited()

    def test_narrate_missing_field_is_422(self, state):
        assert _client(state, control_routes).post("/api/admin/narrate", json={}).status_code == 422

    def test_narrate_without_foundry_is_503(self, state):
        state.foundry_client = None
        r = _client(state, control_routes).post("/api/admin/narrate", json={"text": "hi"})
        assert r.status_code == 503

    def test_narrate_sends_stripped_text_as_gm(self, state):
        state.foundry_client.chat_message.return_value = {"id": 1}
        r = _client(state, control_routes).post("/api/admin/narrate", json={"text": "  The door creaks. "})
        assert r.json() == {"success": True, "result": {"id": 1}}
        state.foundry_client.chat_message.assert_awaited_once_with(text="The door creaks.", speaker="GM")

    def test_narrate_failure_hides_message(self, state):
        state.foundry_client.chat_message.side_effect = RuntimeError("/private")
        r = _client(state, control_routes).post("/api/admin/narrate", json={"text": "x"})
        assert r.status_code == 500 and "private" not in r.text


# ----------------------------------------------------------------- scene

class TestScene:
    def _c(self, state):
        return _client(state, scene_routes)

    def test_background_requires_foundry(self, state):
        state.foundry_client = None
        assert self._c(state).post("/api/scene/background").status_code == 503

    def test_background_named_scene_escapes_input_into_js(self, state):
        state.foundry_client.execute_js.return_value = "ok"
        evil = 'x"); alert(1); ("'
        r = self._c(state).post("/api/scene/background", params={"scene_name": evil, "background_src": "a.webp"})
        js = state.foundry_client.execute_js.await_args.args[0]
        assert r.json() == {"status": "ok", "result": "ok"}
        # the quote is escaped, so the payload cannot terminate the string literal
        assert '\\"); alert(1)' in js and 'getName("x")' not in js

    def test_background_active_scene_when_name_omitted(self, state):
        self._c(state).post("/api/scene/background", params={"background_src": "a.webp"})
        assert "canvas.scene.update" in state.foundry_client.execute_js.await_args.args[0]

    def test_background_failure_is_500(self, state):
        state.foundry_client.execute_js.side_effect = RuntimeError("/secret")
        r = self._c(state).post("/api/scene/background")
        assert r.status_code == 500 and "secret" not in r.text

    def test_switch_requires_foundry(self, state):
        state.foundry_client = None
        assert self._c(state).post("/api/scene/switch", params={"scene_name": "x"}).status_code == 503

    def test_switch_failure_code(self, state):
        with patch("actions.executors.execute_switch_scene", AsyncMock(side_effect=RuntimeError("boom"))):
            r = self._c(state).post("/api/scene/switch", params={"scene_name": "x"})
        assert r.status_code == 500 and r.json()["code"] == "SCENE_SWITCH_FAILED"

    def test_list_scenes_variants(self, state):
        c = self._c(state)
        state.foundry_client.get_scenes.return_value = [{"name": "A"}]
        assert c.get("/api/scenes/list").json() == {"scenes": [{"name": "A"}]}
        state.foundry_client.get_scenes.side_effect = RuntimeError("x")
        assert c.get("/api/scenes/list").json()["code"] == "SCENE_LIST_FAILED"
        state.foundry_client.is_connected = False
        assert c.get("/api/scenes/list").json() == {"scenes": []}

    def test_current_scene_variants(self, state):
        c = self._c(state)
        state.state_tracker.state.current_scene = "Crypt"
        state.foundry_client.get_scene_details.return_value = {"grid": 70}
        state.foundry_client.get_scene_tokens.return_value = [{"id": "t"}]
        assert c.get("/api/scene/current").json() == {
            "name": "Crypt", "details": {"grid": 70}, "tokens": [{"id": "t"}]}
        state.foundry_client.get_scene_details.assert_awaited_with("Crypt")
        state.foundry_client.get_scene_tokens.side_effect = RuntimeError("x")
        assert c.get("/api/scene/current").json()["code"] == "SCENE_DETAILS_FAILED"
        state.foundry_client.is_connected = False
        assert c.get("/api/scene/current").json() == {"name": ""}

    def test_spatial_context_disconnected_503(self, state):
        state.foundry_client.is_connected = False
        r = self._c(state).get("/api/scene/spatial-context")
        assert r.status_code == 503 and r.json()["tokens"] == []

    def test_spatial_context_no_active_scene(self, state):
        state.state_tracker.state.current_scene = None
        assert self._c(state).get("/api/scene/spatial-context").json() == {
            "tokens": [], "error": "No active scene"}

    def test_spatial_context_shapes_tokens_with_defaults(self, state):
        state.foundry_client.get_scene_tokens.return_value = [{"id": "a", "x": 5}]
        state.foundry_client.get_scene_details.return_value = {"grid": None}
        body = self._c(state).get("/api/scene/spatial-context", params={"scene_name": "S"}).json()
        assert body["grid_size"] == 64.0 and body["scene"] == "S"
        assert body["tokens"][0] == {
            "id": "a", "name": "Unknown", "x": 5, "y": 0, "width": 1, "height": 1,
            "disposition": 0, "hidden": False}

    def test_spatial_context_failure_500(self, state):
        state.foundry_client.get_scene_tokens.side_effect = RuntimeError("/secret")
        r = self._c(state).get("/api/scene/spatial-context", params={"scene_name": "S"})
        assert r.status_code == 500 and "secret" not in r.text


# ------------------------------------------------------------------- npc

class TestNpc:
    def _c(self, state):
        return _client(state, npc_routes)

    def test_list_npcs_variants(self, state):
        c = self._c(state)
        state.foundry_client.get_actors.return_value = [{"name": "Borin"}]
        assert c.get("/api/npcs").json() == {"npcs": [{"name": "Borin"}]}
        state.foundry_client.get_actors.assert_awaited_with(world_only=True)
        state.foundry_client.get_actors.side_effect = RuntimeError("/x")
        r = c.get("/api/npcs")
        assert r.status_code == 500 and r.json()["code"] == "NPC_FETCH_FAILED"
        state.foundry_client.is_connected = False
        assert c.get("/api/npcs").json() == {"npcs": []}

    def test_npc_context(self, state):
        c = self._c(state)
        state.state_tracker = MagicMock()
        state.state_tracker.state.npc_context = "Borin is here"
        assert c.get("/api/npc_context").json() == {"context": "Borin is here"}
        state.state_tracker = None
        assert c.get("/api/npc_context").json() == {"context": ""}

    def test_personality_503_and_success(self, state):
        c = self._c(state)
        params = {"npc_id": "n1", "npc_name": "Borin", "description": "gruff"}
        state.personality_engine = None
        assert c.post("/api/npc/personality", params=params).status_code == 503
        p = MagicMock(traits=["gruff"], strengths=["s"], flaws=["f"], motivations=["m"],
                      mannerisms=["x"], speech_pattern="terse")
        state.personality_engine = MagicMock()
        state.personality_engine.parse_npc_description.return_value = p
        body = c.post("/api/npc/personality", params=params).json()
        state.personality_engine.parse_npc_description.assert_called_once_with("n1", "Borin", "gruff")
        assert body["traits"] == ["gruff"] and body["speech_pattern"] == "terse"

    def test_personality_missing_param_422(self, state):
        assert self._c(state).post("/api/npc/personality", params={"npc_id": "n"}).status_code == 422

    def test_npc_context_endpoint(self, state):
        c = self._c(state)
        state.npc_registry = None
        assert c.get("/api/npc/context", params={"npc_id": "n"}).json()["error"]
        state.npc_registry = MagicMock()
        state.npc_registry.get_npc_context.return_value = "ctx"
        assert c.get("/api/npc/context", params={"npc_id": "n"}).json() == {"npc_id": "n", "context": "ctx"}

    def test_register_requires_both_systems(self, state):
        params = {"npc_id": "n", "npc_name": "B", "description": "d"}
        state.npc_registry = MagicMock()
        state.personality_engine = None
        assert self._c(state).post("/api/npc/register", params=params).status_code == 503

    def test_register_stores_npc_and_personality(self, state):
        state.npc_registry = MagicMock()
        state.personality_engine = MagicMock()
        state.personality_engine.parse_npc_description.return_value = MagicMock(traits=["brave"])
        r = self._c(state).post("/api/npc/register", params={
            "npc_id": "n", "npc_name": "B", "description": "d", "level": 3, "class_name": "Fighter"})
        assert r.json() == {"npc_id": "n", "npc_name": "B", "registered": True, "personality": ["brave"]}
        state.npc_registry.register_npc.assert_called_once_with(
            "n", "B", "d", appearance=None, class_name="Fighter", level=3, alignment=None)
        state.npc_registry.set_npc_personality.assert_called_once_with("n", ["brave"])

    def test_register_bad_level_is_422(self, state):
        state.npc_registry = MagicMock()
        state.personality_engine = MagicMock()
        r = self._c(state).post("/api/npc/register", params={
            "npc_id": "n", "npc_name": "B", "description": "d", "level": "high"})
        assert r.status_code == 422
        state.npc_registry.register_npc.assert_not_called()

    def test_relationship_set_and_get(self, state):
        c = self._c(state)
        params = {"source_id": "a", "target_id": "b", "target_name": "B", "relationship_type": "ally"}
        state.npc_registry = None
        assert c.post("/api/npc/relationship", params=params).status_code == 503
        assert c.get("/api/npc/relationships", params={"npc_id": "a"}).json()["error"]

        state.npc_registry = MagicMock()
        state.npc_registry.add_relationship.return_value = MagicMock(strength=0.5)
        assert c.post("/api/npc/relationship", params=params).json()["strength"] == 0.5
        state.npc_registry.add_relationship.assert_called_once_with("a", "b", "B", "ally", 0.5)

        rel = MagicMock(relationship_type="ally", strength=0.9, last_interaction="today")
        state.npc_registry.get_npc_relationships.return_value = {"b": rel}
        assert c.get("/api/npc/relationships", params={"npc_id": "a"}).json() == {
            "npc_id": "a",
            "relationships": {"b": {"type": "ally", "strength": 0.9, "last_interaction": "today"}}}
