"""Shared test/eval harness for the AI-GM engine.

The mocks here drive the full pipeline — session start, player messages,
proactive beats — without a live relay, LLM API, or Foundry server. They are
shared by two consumers:

- ``tests/test_e2e_harness.py`` — deterministic unit-level scenarios.
- ``evals/replay.py`` — the scenario-corpus replay runner, which swaps
  ``ScriptedLLM`` for a real ``LLMManager`` and turns the recorder into a
  transcript.

``MockFoundryClient`` records every call the engine makes; that record is the
"event log" a scenario run produces.
"""

import time
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock


class MockFoundryClient:
    """Records every call made through it; never talks to a relay.

    Fixtures (actors, scene tokens, scene names, world info) are constructor
    parameters so eval scenarios can shape the world the GM believes it is in.
    """

    DEFAULT_ACTORS = [
        {"id": "pc1", "name": "Aria", "type": "character", "has_player_owner": True,
         "uuid": "Actor.pc1"},
    ]
    DEFAULT_TOKENS = [
        {"id": "t1", "name": "Aria", "actorId": "pc1", "x": 100, "y": 100, "hp": 30, "maxHp": 30},
        {"id": "t2", "name": "Goblin", "actorId": "npc1", "x": 200, "y": 200, "hp": 12, "maxHp": 12},
    ]
    DEFAULT_SCENES = ["Test Scene", "The Next Room"]

    def __init__(self, *, actors: Optional[list] = None, tokens: Optional[list] = None,
                 scenes: Optional[list] = None, world_title: str = "Valenthal",
                 scene_name: str = "The Sunken Crypt"):
        self._ai_name = "Sage"
        self.calls: List[Dict] = []
        self._subscribers: Dict[str, list] = {}
        self._session_id: Optional[str] = None
        self._actors = self.DEFAULT_ACTORS if actors is None else actors
        self._tokens = self.DEFAULT_TOKENS if tokens is None else tokens
        self._scenes = self.DEFAULT_SCENES if scenes is None else scenes
        self._world_title = world_title
        self._scene_name = scene_name

    # Mirrors FoundryClient.is_connected; several executors gate on it.
    is_connected = True

    def _record(self, method: str, **kwargs):
        self.calls.append({"method": method, **kwargs})

    def calls_of(self, method: str) -> List[Dict]:
        return [c for c in self.calls if c["method"] == method]

    # --- subscription interface (ChatListener calls these at start) ----------
    async def subscribe_to_channel(self, channel: str):
        self._record("subscribe_to_channel", channel=channel)

    def subscribe(self, channel: str, handler):
        self._subscribers.setdefault(channel, []).append(handler)

    async def emit(self, channel: str, data: dict):
        """Simulate an event arriving from Foundry."""
        for h in self._subscribers.get(channel, []):
            await h(data)

    # --- Foundry API surface -------------------------------------------------
    async def chat_message(self, text: str, speaker: str = "", **kw) -> dict:
        self._record("chat_message", text=text, speaker=speaker)
        return {"success": True}

    async def execute_js(self, code: str, **kw) -> dict:
        self._record("execute_js", code=code[:80])
        # Minimal stubs for scripts the engine calls
        if "game.world" in code:
            return {"result": {"title": self._world_title, "id": self._world_title.lower()}}
        if "canvas?.scene" in code:
            return {"result": {"name": self._scene_name, "hasBackground": True}}
        if "game.actors" in code:
            return {"result": list(self._actors)}
        if "game.scenes" in code:
            return {"result": [{"name": n, "active": n == self._scene_name} for n in self._scenes]}
        if "game.paused" in code or "togglePause" in code:
            return {"result": None}
        return {"result": None}

    async def get_actors(self, world_only: bool = False) -> list:
        self._record("get_actors")
        return list(self._actors)

    async def get_scene_tokens(self, scene_name: str = None) -> list:
        self._record("get_scene_tokens")
        return list(self._tokens)

    async def list_scene_names(self) -> list:
        self._record("list_scene_names")
        return list(self._scenes)

    async def set_active_scene(self, scene_name: str) -> dict:
        self._record("set_active_scene", scene_name=scene_name)
        return {"success": True}

    async def place_token(self, actor_id: str = None, actor_name: str = None,
                          x: int = 0, y: int = 0, **kw) -> dict:
        self._record("place_token", actor_id=actor_id, actor_name=actor_name, x=x, y=y)
        return {"success": True, "tokenId": f"tok_{actor_name or actor_id}"}

    async def create_entity(self, entity_type: str, data: dict) -> dict:
        self._record("create_entity", entity_type=entity_type, name=data.get("name"))
        return {"data": {"_id": "scene_001"}, "id": "scene_001"}

    async def scan_world(self) -> dict:
        self._record("scan_world")
        return {
            "modules": [
                {"id": "midi-qol", "title": "Midi QOL", "active": True},
                {"id": "dae", "title": "Dynamic Active Effects", "active": True},
                {"id": "dfreds-convenient-effects", "title": "DFreds Convenient Effects", "active": False},
            ],
            "actors": [],
            "scenes": [],
        }

    async def start_combat(self, token_ids: List[str] = None) -> dict:
        self._record("start_combat", token_ids=token_ids)
        combatants = [
            {"tokenId": t["id"], "name": t["name"], "initiative": 18 - i}
            for i, t in enumerate(self._tokens)
        ]
        return {"success": True, "combatants": combatants, "combatId": "combat_001"}

    async def update_token(self, token_id: str, updates: dict) -> dict:
        self._record("update_token", token_id=token_id, updates=updates)
        return {"success": True}

    async def roll_dice(self, formula: str, **kw) -> dict:
        self._record("roll_dice", formula=formula)
        return {"success": True, "result": 15, "formula": formula}

    async def roll(self, formula: str, speaker: str = "", **kw) -> dict:
        self._record("roll", formula=formula, speaker=speaker)
        return {"success": True, "result": 15, "formula": formula, "total": 15}

    async def roll_initiative(self, **kw) -> dict:
        self._record("roll_initiative")
        return {"success": True, "order": []}

    async def start_encounter(self, tokens=None, **kw) -> dict:
        # Legacy alias — executor now calls start_combat
        return await self.start_combat(token_ids=tokens or [])

    async def update_hp(self, token_id: str, delta: int, **kw) -> dict:
        self._record("update_hp", token_id=token_id, delta=delta)
        return {"success": True}

    async def configure_scene(self, updates: dict, scene_name: str = None, **kw) -> dict:
        self._record("configure_scene", updates=updates, scene_name=scene_name)
        return {"success": True}

    async def wait_for_hook(self, hook_name: str, timeout: float = 10, **kw) -> bool:
        self._record("wait_for_hook", hook=hook_name)
        return True

    async def request_long_rest(self, actor_uuid: str, **kw) -> dict:
        self._record("request_long_rest", actor_uuid=actor_uuid)
        return {"success": True}

    async def whisper(self, player_id: str, message: str, **kw) -> dict:
        self._record("whisper", player_id=player_id, text=message)
        return {"success": True}

    async def decrease_attribute(self, path: str, amount: int, target_uuid: str, **kw) -> dict:
        self._record("decrease_attribute", path=path, amount=amount, target_uuid=target_uuid)
        return {"success": True}

    async def increase_attribute(self, path: str, amount: int, target_uuid: str, **kw) -> dict:
        self._record("increase_attribute", path=path, amount=amount, target_uuid=target_uuid)
        return {"success": True}

    def reset_message_id(self):
        pass

    def _get_speaker_name(self) -> str:
        return self._ai_name


class ScriptedLLM:
    """Returns a pre-written action sequence instead of calling the real LLM.

    Responses are consumed in order; the last one repeats for any extra calls.
    """

    def __init__(self, responses: List[Dict]):
        self._responses = responses
        self._idx = 0
        self.calls: List[str] = []
        self.model = "mock-model"
        self._conversation_history: List[Dict] = []
        self._system_prompt_cache = None
        self._active_modules: List[str] = []

    @property
    def conversation_history(self) -> List[Dict]:
        return self._conversation_history

    @property
    def system_prompt(self) -> str:
        return "You are a mock GM."

    def invalidate_system_prompt(self):
        self._system_prompt_cache = None

    def set_active_modules(self, modules: List[str]):
        self._active_modules = modules

    def set_system_prompt(self, prompt: str):
        self._system_prompt_cache = prompt

    def set_dynamic_npc_context(self, context: str):
        pass

    def set_dynamic_world_context(self, context: str):
        pass

    def set_dynamic_house_rules_context(self, context: str):
        pass

    def set_dynamic_canon_context(self, context: str):
        pass

    async def generate(self, user_message: str, game_state_summary: str = "",
                       extra_context: str = "") -> Dict:
        self.calls.append(user_message)
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp

    async def close(self):
        pass

    def _trim_history(self):
        pass


class RecordingLLM:
    """Wraps any LLM-like object and records every generate() exchange.

    This is what turns a run against a real model into a transcript: the
    prompt, the parsed response, and the latency of every call land in
    ``self.calls`` alongside the Foundry event log.
    """

    def __init__(self, inner: Any):
        self._inner = inner
        self.calls: List[Dict] = []

    async def generate(self, user_message: str, game_state_summary: str = "",
                       extra_context: str = "") -> Dict:
        start = time.perf_counter()
        resp = await self._inner.generate(
            user_message,
            game_state_summary=game_state_summary,
            extra_context=extra_context,
        )
        self.calls.append({
            "user_message": user_message,
            "game_state_summary": game_state_summary,
            "extra_context": extra_context,
            "response": resp,
            "latency_s": round(time.perf_counter() - start, 3),
            "model": getattr(self._inner, "model", "unknown"),
        })
        return resp

    async def close(self):
        close = getattr(self._inner, "close", None)
        if close:
            await close()

    def __getattr__(self, name: str):
        # Delegate everything else (system_prompt, set_dynamic_*_context,
        # conversation_history, set_active_modules, ...) to the wrapped LLM.
        return getattr(self._inner, name)


class MockDatabase:
    """In-memory DB stub — enough for ChatListener and the session checks."""

    def __init__(self):
        self._active: Optional[str] = None
        self._sessions: Dict[str, dict] = {}
        self._conversations: List[dict] = []

    async def get_active_session(self) -> Optional[str]:
        return self._active

    async def create_session(self, session_id: str, campaign: str) -> str:
        self._sessions[session_id] = {"id": session_id, "campaign": campaign}
        self._active = session_id
        return session_id

    async def save_conversation(self, session_id: str, role: str, content: str):
        self._conversations.append({"session": session_id, "role": role, "content": content})

    async def end_session(self, session_id: str):
        if self._active == session_id:
            self._active = None

    async def record_typed_event(self, session_id: str, event_type: str, payload: dict, description: str = ""):
        """Record event for event sourcing (stub for e2e harness)."""
        pass


class MockStateTracker:
    """Minimal GameStateTracker stand-in."""

    def __init__(self, *, mode: str = "exploration", scene: str = "The Sunken Crypt"):
        self.state = MagicMock()
        self.state.mode = mode
        self.state.scene = scene

    def get_snapshot(self) -> str:
        return f"mode={self.state.mode} scene={self.state.scene}"

    def get_encounter_context(self) -> str:
        return ""

    async def set_campaign(self, name: str):
        pass

    async def set_mode(self, mode):
        self.state.mode = str(mode)

    async def save(self):
        pass


class MockNPCRegistry:
    """Name -> NPC record lookup, seedable per scenario."""

    def __init__(self, npcs: Optional[Dict[str, Dict[str, str]]] = None):
        self._npcs: Dict[str, Any] = {}
        for name, fields in (npcs or {"Goblin": {
            "description": "A small, vicious goblin",
            "personality_traits": "Cowardly but vicious in a pack",
            "combat_style": "Hits and runs; flees when outnumbered",
        }}).items():
            rec = MagicMock()
            for key, value in fields.items():
                setattr(rec, key, value)
            self._npcs[name] = rec

    def get_npc_by_name(self, name: str):
        return self._npcs.get(name)

    def list_npcs(self) -> list:
        """All registered NPCs (records expose .goals, empty by default)."""
        return list(self._npcs.values())

    def register_npc(self, **kw):
        pass


def build_listener(llm, foundry: MockFoundryClient, db: MockDatabase,
                   state: MockStateTracker, npc_registry=None):
    """Instantiate ChatListener with all real subsystems except LLM + Foundry."""
    from actions.dispatcher import ActionDispatcher
    from foundry.chat_listener import ChatListener

    dispatcher = ActionDispatcher(foundry)

    listener = ChatListener(
        foundry=foundry,
        llm=llm,
        dispatcher=dispatcher,
        state_tracker=state,
        db=db,
        npc_registry=npc_registry or MockNPCRegistry(),
    )
    return listener
