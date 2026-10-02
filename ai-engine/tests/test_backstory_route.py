"""Tests for POST /api/backstory (api/routes/backstory.py): lore grounding,
validation and failure modes. Direct-call convention, as in test_canon_routes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from api.routes.backstory import BackstoryRequest, generate_backstory
from llm.usage import TokenBudgetExceeded


def _state(llm_text="She left the Saltmarsh at nineteen.", hits=None, world="The Saltmarsh coast."):
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value=llm_text)
    rag = MagicMock()
    rag.inject_lore = AsyncMock(return_value=hits if hits is not None else [])
    loader = MagicMock()
    loader.get_world_context_sync = MagicMock(return_value=world)
    return SimpleNamespace(llm_manager=llm, semantic_rag=rag, campaign_loader=loader)


def _req(**kw):
    return BackstoryRequest(**{"name": "Elara", "ancestry": "Elf", "char_class": "Wizard", "homeland": "Saltmarsh", **kw})


def _body(resp):
    return json.loads(resp.body)


def test_backstory_is_grounded_in_world_and_vault_lore():
    hit = SimpleNamespace(text="The Tidewardens guard the Saltmarsh locks.", source="Factions/Tidewardens.md")
    state = _state(hits=[hit])

    result = asyncio.run(generate_backstory(_req(), state))

    assert result["backstory"] == "She left the Saltmarsh at nineteen."
    assert result["sources"] == ["Factions/Tidewardens.md"]
    assert result["grounded"] is True
    kwargs = state.llm_manager.generate_text.call_args.kwargs
    assert "The Saltmarsh coast." in kwargs["context"]
    assert "Tidewardens guard" in kwargs["context"]
    assert "Name: Elara" in kwargs["user_message"] and "Class: Wizard" in kwargs["user_message"]


def test_backstory_still_works_with_no_vault():
    state = _state(world="")
    state.semantic_rag = None

    result = asyncio.run(generate_backstory(_req(), state))

    assert result["status"] == "ok" and result["grounded"] is False
    assert state.llm_manager.generate_text.call_args.kwargs["context"] == ""


def test_lore_lookup_failure_does_not_fail_the_request():
    state = _state()
    state.semantic_rag.inject_lore = AsyncMock(side_effect=RuntimeError("index down"))

    result = asyncio.run(generate_backstory(_req(), state))

    assert result["status"] == "ok" and result["sources"] == []


def test_blank_name_is_rejected_without_calling_the_llm():
    state = _state()

    resp = asyncio.run(generate_backstory(_req(name="  "), state))

    assert resp.status_code == 422
    state.llm_manager.generate_text.assert_not_called()


def test_engine_not_ready_returns_503():
    state = _state()
    state.llm_manager = None

    assert asyncio.run(generate_backstory(_req(), state)).status_code == 503


def test_budget_exhausted_and_llm_failure_have_distinct_errors():
    state = _state()
    state.llm_manager.generate_text = AsyncMock(side_effect=TokenBudgetExceeded("session", 10, 5, 12))
    resp = asyncio.run(generate_backstory(_req(), state))
    assert resp.status_code == 503 and _body(resp)["code"] == "BUDGET"

    state.llm_manager.generate_text = AsyncMock(side_effect=RuntimeError("boom"))
    resp = asyncio.run(generate_backstory(_req(), state))
    assert resp.status_code == 502 and _body(resp)["code"] == "LLM_ERROR"
