import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foundry.character import character_from_concept
from foundry.client import FoundryClient


def test_concept_is_constrained_to_level_one_and_extracts_name():
    result = character_from_concept("Mira, a quiet wizard who studies stars", "Adventurer")
    assert result["name"] == "Mira"
    assert result["class"] == "wizard"
    assert result["level"] == 1


def test_create_player_character_uses_foundry_and_returns_result():
    client = FoundryClient()
    captured = {}

    async def execute_js(script, **kwargs):
        captured["script"] = script
        return {"result": {"ok": True, "uuid": "Actor.abc", "userId": "u1"}}

    client.execute_js = execute_js
    result = asyncio.run(client.create_player_character({"name": "Mira", "class": "wizard"}, "u1"))
    assert result["ok"] is True
    assert "Actor.create" in captured["script"]
    assert "type: 'character'" in captured["script"]
    assert "user.update" in captured["script"]
    assert "dnd5e" in captured["script"]
