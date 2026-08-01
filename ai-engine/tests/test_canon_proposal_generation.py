"""Tests for context/canon.py — canon-proposal generation and parsing."""

import asyncio
import json

from context.canon import (
    build_canon_proposal_prompt,
    generate_canon_proposals,
    parse_canon_proposals,
)


def test_parse_canon_proposals_happy_path():
    text = json.dumps({
        "proposals": [
            {"fact": "The king was a doppelganger.", "confidence": "high",
             "rationale": "Revealed and confirmed.", "contradiction_note": None},
            {"fact": "The bridge is impassable.", "confidence": "medium",
             "rationale": "Destroyed this session.", "contradiction_note": None},
        ]
    })
    proposals = parse_canon_proposals(text)
    assert len(proposals) == 2
    assert proposals[0]["fact"] == "The king was a doppelganger."
    assert proposals[0]["confidence"] == "high"
    assert proposals[1]["contradiction_note"] is None


def test_parse_canon_proposals_flags_contradiction():
    text = json.dumps({"proposals": [
        {"fact": "The king is dead.", "confidence": "high", "rationale": "r",
         "contradiction_note": "conflicts with: the king was crowned last session"},
    ]})
    proposals = parse_canon_proposals(text)
    assert proposals[0]["contradiction_note"] == "conflicts with: the king was crowned last session"


def test_parse_canon_proposals_defaults_invalid_confidence_to_low():
    text = json.dumps({"proposals": [
        {"fact": "Something happened.", "confidence": "extremely sure", "rationale": "r"},
    ]})
    proposals = parse_canon_proposals(text)
    assert proposals[0]["confidence"] == "low"


def test_parse_canon_proposals_drops_entries_missing_fact():
    text = json.dumps({"proposals": [
        {"confidence": "high", "rationale": "no fact field"},
        {"fact": "", "confidence": "high", "rationale": "empty fact"},
        {"fact": "Valid fact.", "confidence": "low", "rationale": "r"},
    ]})
    proposals = parse_canon_proposals(text)
    assert len(proposals) == 1
    assert proposals[0]["fact"] == "Valid fact."


def test_parse_canon_proposals_tolerates_markdown_fence():
    text = "```json\n" + json.dumps({"proposals": [{"fact": "A fact.", "confidence": "low", "rationale": "r"}]}) + "\n```"
    proposals = parse_canon_proposals(text)
    assert len(proposals) == 1


def test_parse_canon_proposals_empty_list_is_valid():
    """Zero proposals is a legitimate, expected outcome — not a parse failure."""
    text = json.dumps({"proposals": []})
    assert parse_canon_proposals(text) == []


def test_parse_canon_proposals_malformed_json_degrades_to_empty_list():
    assert parse_canon_proposals("not json at all") == []
    assert parse_canon_proposals("") == []
    assert parse_canon_proposals(json.dumps({"no_proposals_key": True})) == []


def test_build_canon_proposal_prompt_includes_highlights_and_existing_canon():
    system, user = build_canon_proposal_prompt(
        ["The dragon fled north.", "The party found the amulet."],
        "- The king rules from Kalaman.",
    )
    assert "canon" in system.lower()
    assert "The dragon fled north." in user
    assert "The king rules from Kalaman." in user


def test_build_canon_proposal_prompt_handles_empty_inputs():
    system, user = build_canon_proposal_prompt([], "")
    assert "no highlights recorded" in user.lower()
    assert "no canon established yet" in user.lower()


class _FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeLLMClient:
    def __init__(self, content):
        self._content = content
        self.calls = 0

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        return _FakeResponse(self._content)


def test_generate_canon_proposals_returns_empty_without_highlights():
    client = _FakeLLMClient("{}")
    result = asyncio.run(generate_canon_proposals(
        client, "http://fake/v1/chat/completions", {}, "m", [], ""
    ))
    assert result == []
    assert client.calls == 0  # no highlights -> skip the LLM call entirely


def test_generate_canon_proposals_parses_real_response():
    client = _FakeLLMClient(json.dumps({"proposals": [
        {"fact": "The tower collapsed.", "confidence": "high", "rationale": "r", "contradiction_note": None},
    ]}))
    result = asyncio.run(generate_canon_proposals(
        client, "http://fake/v1/chat/completions", {}, "m", ["The tower fell during the fight."], ""
    ))
    assert len(result) == 1
    assert result[0]["fact"] == "The tower collapsed."


def test_generate_canon_proposals_never_raises_on_llm_failure():
    class _RaisingClient:
        async def post(self, *a, **kw):
            raise RuntimeError("connection refused")

    result = asyncio.run(generate_canon_proposals(
        _RaisingClient(), "http://fake/v1/chat/completions", {}, "m", ["something happened"], ""
    ))
    assert result == []
