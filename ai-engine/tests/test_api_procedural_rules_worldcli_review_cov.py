"""Behavioural coverage for api/routes/{procedural,rules,world_cli}.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import procedural as procedural_routes
from api.routes import rules as rules_routes
from api.routes import world_cli as wc_routes
from config import settings
from foundry.world_cli import WorldCLIError
from foundry.world_cli_setup import SetupError


@pytest.fixture
def state():
    return AppState()


def _client(state, *routers):
    app = FastAPI()
    for r in routers:
        app.include_router(r.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _h(request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump())

    return TestClient(app, raise_server_exceptions=False)


# ------------------------------------------------------------- procedural

class TestEncounter:
    def test_uninitialised_components_are_503_not_500(self, state):
        c = _client(state, procedural_routes)
        assert c.get("/api/procedural/encounter").status_code == 503
        state.action_dispatcher = AsyncMock()
        assert c.get("/api/procedural/encounter").status_code == 503
        state.action_dispatcher.execute.assert_not_awaited()

    def test_dispatches_a_validated_generate_encounter_action(self, state):
        state.action_dispatcher = AsyncMock()
        state.action_dispatcher.execute.return_value = {"success": True}
        state.foundry_client = object()
        r = _client(state, procedural_routes).get(
            "/api/procedural/encounter", params={"difficulty": "hard", "party_level": 7, "party_size": 3})
        assert r.json() == {"success": True}
        state.action_dispatcher.execute.assert_awaited_once_with({
            "type": "generate_encounter", "party_level": 7, "party_size": 3, "difficulty": "hard"})

    def test_failure_is_type_only(self, state):
        state.action_dispatcher = AsyncMock()
        state.action_dispatcher.execute.side_effect = RuntimeError("/secret")
        state.foundry_client = object()
        b = _client(state, procedural_routes).get("/api/procedural/encounter").json()
        assert b["success"] is False and "secret" not in str(b)


class TestGenerators:
    def test_treasure_shape(self, state):
        t = _client(state, procedural_routes).get(
            "/api/procedural/treasure", params={"treasure_cr": 5, "level": 8}).json()["treasure"]
        assert set(t) == {"gold", "gems", "items", "magical_items", "total_value"}

    def test_npc_and_party_shapes(self, state):
        c = _client(state, procedural_routes)
        npc = c.get("/api/procedural/npc").json()["npc"]
        assert npc["name"] and npc["race"] and "class" in npc
        party = c.get("/api/procedural/party", params={"size": 4, "level": 3}).json()["party"]
        assert len(party) == 4 and {p["level"] for p in party} == {3}
        assert {"Fighter", "Rogue", "Cleric", "Wizard"} <= {p["class"] for p in party}

    def test_quest_theme_is_passed_through(self, state):
        with patch("procedural.quests.QuestGenerator.generate") as gen:
            gen.return_value = MagicMock(title="T", description="d", quest_giver="g", objective="o",
                                         reward="r", complications=["c"], resolution_options=["x"])
            q = _client(state, procedural_routes).get("/api/procedural/quest", params={"theme": "undead"}).json()["quest"]
        gen.assert_called_once_with("undead")
        assert q["title"] == "T" and q["complications"] == ["c"]

    def test_session_has_two_encounters_two_quests_four_npcs(self, state):
        s = _client(state, procedural_routes).get(
            "/api/procedural/session", params={"party_level": 4, "party_size": 5}).json()["session"]
        assert [e["difficulty"] for e in s["encounters"]] == ["medium", "hard"]
        assert len(s["quests"]) == 2 and len(s["npcs"]) == 4

    def test_dungeon_has_one_level_per_floor(self, state):
        d = _client(state, procedural_routes).post(
            "/api/procedural/dungeon/multi-level",
            params={"name": "Deep", "floors": 2, "width": 40, "height": 40}).json()["dungeon"]
        assert d["name"] == "Deep" and d["floors"] == 2 and "Level Manager" in d["import_instructions"]

    def test_dungeon_failure_is_type_only(self, state):
        with patch("procedural.layout_gen.MultiLevelDungeonGenerator.generate_multi_level_dungeon",
                   side_effect=ValueError("/secret")):
            b = _client(state, procedural_routes).post("/api/procedural/dungeon/multi-level").json()
        assert b["success"] is False and "secret" not in str(b)


class TestSizeBoundsProtectTheServer:
    """Unbounded counts let one request pin the event loop, so they are rejected up front."""

    @pytest.mark.parametrize("method,path,params,target", [
        ("get", "/api/procedural/party", {"size": 10**9}, "procedural.npcs.NPCGenerator.generate_party"),
        ("get", "/api/procedural/party", {"size": -1}, "procedural.npcs.NPCGenerator.generate_party"),
        ("post", "/api/procedural/dungeon/multi-level", {"floors": 10**6},
         "procedural.layout_gen.MultiLevelDungeonGenerator.generate_multi_level_dungeon"),
        ("post", "/api/procedural/dungeon/multi-level", {"width": 10**6},
         "procedural.layout_gen.MultiLevelDungeonGenerator.generate_multi_level_dungeon"),
        ("post", "/api/procedural/dungeon/multi-level", {"height": 0},
         "procedural.layout_gen.MultiLevelDungeonGenerator.generate_multi_level_dungeon"),
    ])
    def test_out_of_range_is_422_and_nothing_is_generated(self, state, method, path, params, target):
        with patch(target) as gen:
            r = getattr(_client(state, procedural_routes), method)(path, params=params)
        assert r.status_code == 422
        gen.assert_not_called()


# ------------------------------------------------------------------ rules

class TestRules:
    def test_srd_search(self, state):
        c = _client(state, rules_routes)
        assert c.get("/api/srd/search", params={"query": "grapple"}).json() == {"results": ""}
        state.campaign_loader = MagicMock()
        state.campaign_loader.search_srd = AsyncMock(return_value=["Grapple rules"])
        r = c.get("/api/srd/search", params={"query": "grapple", "max_results": 1})
        assert r.json() == {"results": ["Grapple rules"]}
        state.campaign_loader.search_srd.assert_awaited_once_with("grapple", 1)
        assert c.get("/api/srd/search").status_code == 422

    def test_spell_found_and_missing(self, state):
        c = _client(state, rules_routes)
        hit = c.get("/api/rules/spell", params={"name": "Fireball"}).json()
        assert hit["found"] is True and hit["spell"]
        miss = c.get("/api/rules/spell", params={"name": "Zzzz Not A Spell"}).json()
        assert miss == {"spell": None, "found": False}

    def test_spell_search(self, state):
        r = _client(state, rules_routes).get("/api/rules/spells", params={"query": "fire"}).json()
        assert isinstance(r["spells"], (list, dict)) and r["spells"]

    def test_condition_found_and_missing(self, state):
        c = _client(state, rules_routes)
        hit = c.get("/api/rules/condition", params={"name": "Prone"}).json()
        assert hit["found"] is True and hit["description"]
        assert c.get("/api/rules/condition", params={"name": "Sleepy"}).json() == {
            "condition": "Sleepy", "description": None, "found": False}

    def test_dc_and_reference(self, state):
        c = _client(state, rules_routes)
        easy = c.get("/api/rules/dc", params={"difficulty": "easy"}).json()["dc"]
        hard = c.get("/api/rules/dc", params={"difficulty": "hard"}).json()["dc"]
        assert easy < hard
        assert c.get("/api/rules/reference").json()["rules"]


# -------------------------------------------------------------- world_cli

class TestWorldCli:
    def _c(self, state):
        return _client(state, wc_routes)

    def test_pair_disabled_and_not_connected(self, state):
        c = self._c(state)
        assert c.post("/api/world-cli/pair", json={}).json()["code"] == "DISABLED"
        state.world_cli = AsyncMock()
        r = c.post("/api/world-cli/pair", json={})
        assert r.status_code == 503 and r.json()["code"] == "NO_FOUNDRY"
        state.foundry_client = MagicMock(is_connected=False)
        assert c.post("/api/world-cli/pair", json={}).json()["code"] == "NO_FOUNDRY"

    def _paired(self, state):
        state.world_cli = AsyncMock()
        state.foundry_client = MagicMock(is_connected=True)

    @pytest.mark.parametrize("writes,expected", [
        (True, ["scene.wall.delete-many", "scene.light.delete-many", "scene.sound.delete-many"]),
        (False, [])])
    def test_pair_default_allow_list_depends_on_write_mode(self, state, monkeypatch, writes, expected):
        self._paired(state)
        monkeypatch.setattr(settings, "world_cli_writes_enabled", writes)
        with patch.object(wc_routes, "pair_world_cli", AsyncMock(return_value={"export": "X=1"})) as pair:
            r = self._c(state).post("/api/world-cli/pair", json={})
        assert r.json() == {"status": "ok", "export": "X=1"}
        assert pair.await_args.args[2] == expected

    def test_pair_explicit_allow_list_is_used(self, state, monkeypatch):
        self._paired(state)
        monkeypatch.setattr(settings, "world_cli_writes_enabled", True)
        with patch.object(wc_routes, "pair_world_cli", AsyncMock(return_value={})) as pair:
            self._c(state).post("/api/world-cli/pair", json={"allow_commands": ["scene.tile.delete-many"]})
        assert pair.await_args.args[2] == ["scene.tile.delete-many"]

    @pytest.mark.parametrize("cmd", ["not a command", "macro.execute", "user.role.set", "scene.region.behavior.executable.add"])
    def test_pair_refuses_malformed_or_privileged_commands(self, state, cmd):
        """Code-running / access-changing commands can never be pre-approved."""
        self._paired(state)
        with patch.object(wc_routes, "pair_world_cli", AsyncMock()) as pair:
            r = self._c(state).post("/api/world-cli/pair", json={"allow_commands": [cmd]})
        assert r.status_code == 400 and r.json()["code"] == "SETUP_FAILED"
        pair.assert_not_awaited()

    def test_pair_too_many_commands_is_422(self, state):
        self._paired(state)
        r = self._c(state).post("/api/world-cli/pair", json={"allow_commands": ["scene.a.b"] * 51})
        assert r.status_code == 422

    def test_pair_setup_error_hides_detail_and_world_cli_errors_map_to_status(self, state):
        self._paired(state)
        with patch.object(wc_routes, "pair_world_cli", AsyncMock(side_effect=SetupError("Nope", detail="secret-token"))):
            r = self._c(state).post("/api/world-cli/pair", json={"allow_commands": []})
        assert r.status_code == 400 and r.json()["error"] == "Nope" and "secret" not in r.text
        for code, status in (("DAEMON_UNAVAILABLE", 503), ("COMMAND_DENIED", 409), ("OTHER", 400)):
            with patch.object(wc_routes, "pair_world_cli", AsyncMock(side_effect=WorldCLIError(code, "m"))):
                assert self._c(state).post("/api/world-cli/pair", json={"allow_commands": []}).status_code == status

    def test_status_and_audit_files(self, state):
        c = self._c(state)
        assert c.get("/api/world-cli/status").status_code == 503
        assert c.get("/api/world-cli/audit-files").status_code == 503
        state.world_cli = MagicMock()
        state.world_cli.call = AsyncMock(return_value={"bridge": "up"})
        assert c.get("/api/world-cli/status").json() == {"status": "ok", "bridge": "up"}
        state.world_cli.call = AsyncMock(return_value={"missing": []})
        c.get("/api/world-cli/audit-files", params={"limit": 5, "offset": 2, "scope": ["scenes", "actors"]})
        state.world_cli.call.assert_awaited_once_with(
            "world.audit-files", {"limit": 5, "offset": 2, "scope": ["scenes", "actors"]})
        assert c.get("/api/world-cli/audit-files", params={"limit": 0}).status_code == 422
        assert c.get("/api/world-cli/audit-files", params={"limit": 501}).status_code == 422
        state.world_cli.call = AsyncMock(side_effect=WorldCLIError("TIMEOUT", "slow"))
        assert c.get("/api/world-cli/status").status_code == 503
        assert c.get("/api/world-cli/audit-files").status_code == 503
