"""Smaller follow-ups from the 2026-10-02 coverage review: each was red before its fix."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import foundry.client as fc
from api.deps import AppState, get_app_state
from api.routes import session as session_routes
from api.routes import system as system_routes
from api.routes import npc as npc_routes
from foundry.client import FoundryClient


def run(coro):
    return asyncio.run(coro)


# --- connect(): a bad handshake must back off like any other failure -----------------------

class _WS:
    def __init__(self, ack):
        self._ack = ack
        self.closed = 0

    async def send(self, _):
        pass

    async def recv(self):
        return json.dumps(self._ack)

    async def close(self):
        self.closed += 1


def test_a_bad_ack_backs_off_between_attempts(monkeypatch):
    sleeps = []

    async def connect(url):
        return _WS({"type": "error"})

    async def sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(fc.websockets, "connect", connect)
    monkeypatch.setattr(fc.asyncio, "sleep", sleep)
    c = FoundryClient()
    assert run(c.connect(max_retries=3)) is False
    assert sleeps == [2, 4]                       # not three instant retries
    assert "Unexpected auth response" in c._last_connect_error


# --- API guards -------------------------------------------------------------------------------

def _app(state, *routers):
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


def test_state_update_without_a_tracker_is_a_503_not_a_500():
    r = _app(AppState(), session_routes.router).post("/api/state/update", json={"scene": "x"})
    assert r.status_code == 503


def test_memory_rebuild_failure_is_reported_not_a_bare_500_traceback():
    state = AppState()
    state.campaign_memory = SimpleNamespace(rebuild=AsyncMock(side_effect=RuntimeError("db locked")))
    r = _app(state, system_routes.router).post("/api/memory/rebuild", params={"campaign": "c"})
    assert r.status_code == 500 and "db locked" not in r.text and r.json().get("error")


def test_relationship_strength_is_bounded_to_the_documented_0_1_range():
    state = AppState()
    state.npc_registry = MagicMock()
    c = _app(state, npc_routes.router)
    base = {"source_id": "a", "target_id": "b", "target_name": "B", "relationship_type": "ally"}
    assert c.post("/api/npc/relationship", params={**base, "strength": 5}).status_code == 422
    assert c.post("/api/npc/relationship", params={**base, "strength": -1}).status_code == 422
    assert c.post("/api/npc/relationship", params={**base, "strength": "nan"}).status_code == 422


def test_registry_clamps_strength_for_every_caller():
    from npc.registry import NPCRegistry
    reg = NPCRegistry.__new__(NPCRegistry)
    reg.relationships = {}
    reg.get_npc = lambda _id: None
    assert reg.add_relationship("a", "b", "B", "ally", 9.0).strength == 1.0
    assert reg.add_relationship("a", "c", "C", "foe", -3.0).strength == 0.0


# --- import dedupe must never drop an item that shares a name ---------------------------------

def test_semantic_dedupe_keeps_distinct_npcs_who_share_a_name(monkeypatch):
    import campaign.importer as imp
    from campaign.orchestrator_import import WorldImportMixin
    o = WorldImportMixin.__new__(WorldImportMixin)
    o.settings = SimpleNamespace(model="m", llm_api_key="k")
    o._chat_endpoint = lambda: "http://x/v1/chat/completions"
    o._suppress_thinking = lambda p: None
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: {"choices": [{"message": {"content": "{}"}}]}
    llm = MagicMock()
    llm.post = AsyncMock(return_value=resp)
    monkeypatch.setattr(imp, "parse_dedup_groups", lambda text, names: [[n] for n in names])
    items = [{"name": "Marta", "role": "smith"}, {"name": "Marta", "role": "queen"}, {"name": "Orin"}, {"name": "Orin the Old"}]
    out = run(o._semantic_dedupe_section(llm, "npc", items))
    assert len(out) == 4
    assert sorted(i["role"] for i in out if i["name"] == "Marta") == ["queen", "smith"]
    # only the uniquely-named entries went to the judge
    assert "Marta" not in llm.post.await_args.kwargs["json"]["messages"][1]["content"]


# --- small robustness fixes ---------------------------------------------------------------------

def test_a_unicode_digit_in_resolved_ids_is_ignored_not_a_crash():
    from context.campaign_memory import CampaignMemory
    m = CampaignMemory.__new__(CampaignMemory)
    m.db = MagicMock()
    m.db.add_memory_node = AsyncMock(return_value=1)
    m.db.add_memory_facts = AsyncMock()
    m.db.resolve_memory_facts = AsyncMock()
    rows = [{"id": 1}, {"id": 2}]
    run(m._store("c", "s", rows, {"summary": "x", "resolved": ["\u00b2", "3", 4]}, [{"id": 3}, {"id": 4}]))
    assert m.db.resolve_memory_facts.await_args.args == ("c", [3, 4])


def test_a_chat_message_with_a_null_speaker_falls_back_to_the_author_not_the_word_None():
    from foundry.chat_listener import GameLoop
    ll = GameLoop(foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
                  state_tracker=MagicMock(), db=MagicMock())
    ll._gm_user_ids = {"gm1"}
    inner = {"speaker": None, "author": {"id": "u1", "name": "Alice"}}
    assert ll._speaker_name(inner) == "Alice"


def test_announce_initiative_survives_an_unknown_turn_order_id():
    from combat.loop import CombatLoop
    loop = CombatLoop.__new__(CombatLoop)
    loop._turn_order = ["ghost", "n"]
    loop._pc_tokens = []
    loop._npc_tokens = [{"id": "n", "name": "Gob"}]
    loop.foundry = MagicMock()
    loop.foundry.chat_message = AsyncMock()
    run(loop._announce_initiative())
    msg = loop.foundry.chat_message.await_args.args[0]
    assert "Unknown" in msg and "Gob" in msg       # the unknown id no longer loses the whole announcement
