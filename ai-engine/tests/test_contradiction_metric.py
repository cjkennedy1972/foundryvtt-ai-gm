"""Tests for the contradiction metric (CKP-97) — the one number v2.0 optimises.

Covers the three detectors (authored canon patterns, event-log vitality, the
LLM judge), the metrics history/publish machinery, and the off-session tick
gate ("contradiction rate must not rise with tick volume").
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import judge as judge_mod
from evals import metrics as metrics_mod
from evals import replay as replay_mod
from evals import score as score_mod
from evals.contradictions import (
    extract_dead_entities,
    scan_event_log,
    scan_turns,
)


def _chat(text, speaker=""):
    return {"method": "chat_message", "text": text, "speaker": speaker}


# ---------------------------------------------------------------------------
# Vitality extraction from canon fact text
# ---------------------------------------------------------------------------

def test_extract_dead_entities_copula_and_passive():
    facts = [
        "Brother Fenwick is dead; the party buried him three days ago.",
        "Marta the smith was slain by ghouls.",
        "The bridge over the Arn has burned down.",  # a place, not a person
        "The party buried Henrik behind the chapel.",
        "The door is locked with a rusted padlock.",  # no vitality assertion
    ]
    dead = extract_dead_entities(facts)
    assert "Brother Fenwick" in dead
    assert "Marta the smith" in dead or "Marta" in dead
    assert "Henrik" in dead
    assert not any("bridge" in d.lower() or "door" in d.lower() for d in dead)


def test_extract_dead_entities_ignores_the_living():
    assert extract_dead_entities(["The innkeeper is named Borin."]) == []
    assert extract_dead_entities(["Kael is missing, presumed alive."]) == []


# ---------------------------------------------------------------------------
# Event-log detector: dead things stay dead
# ---------------------------------------------------------------------------

_FACTS = ["Brother Fenwick is dead; the party buried him three days ago."]


def test_dead_speaker_is_flagged():
    calls = [_chat("Blessings, child.", speaker="Brother Fenwick")]
    hits = scan_event_log(calls, _FACTS)
    assert len(hits) == 1
    assert hits[0].source == "event"
    assert "Fenwick" in hits[0].detail


def test_dead_npc_narrated_acting_is_flagged():
    calls = [_chat("Fenwick nods at you from the chapel door.")]
    hits = scan_event_log(calls, _FACTS)
    assert any("dead per canon" in h.detail for h in hits)


def test_remnant_mentions_are_not_contradictions():
    clean = [
        _chat("Out back, the fresh-turned earth of his grave has settled."),
        _chat("Fenwick's prayer book still lies open on the lectern."),
        _chat("A statue of Fenwick stands in the nave, worn smooth."),
        _chat("The ghost of Fenwick appears at vespers."),  # undead ≠ alive
    ]
    assert scan_event_log(clean, _FACTS) == []


def test_living_npcs_are_untouched():
    calls = [_chat("Borin waves you over.", speaker="Borin")]
    assert scan_event_log(calls, _FACTS) == []


def test_scan_turns_uses_prior_events_as_ground_truth():
    """The tick API: an event that says someone died is as binding as canon."""
    turns = ["Henrik waves from the dock."]
    assert scan_turns(turns, [], prior_events=["The party buried Henrik."])
    assert not scan_turns(turns, [], prior_events=["Henrik sailed north."])


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

def test_parse_verdict_tolerates_fences_and_commentary():
    assert judge_mod.parse_verdict(
        '{"contradiction": true, "reason": "door is locked"}').contradiction
    fenced = "```json\n{\"contradiction\": false, \"reason\": \"ok\"}\n```"
    assert not judge_mod.parse_verdict(fenced).contradiction
    chatty = 'Sure! {"contradiction": true, "reason": "x"} hope this helps'
    assert judge_mod.parse_verdict(chatty).contradiction
    assert judge_mod.parse_verdict("no json here") is None
    assert judge_mod.parse_verdict('{"contradiction": "yes"}') is None


def test_judge_prompt_carries_canon_prior_and_turn():
    prompt = judge_mod.build_judge_prompt(
        ["The door is locked."], ["You knock."], "The door swings open.")
    assert "The door is locked." in prompt
    assert "You knock." in prompt
    assert "The door swings open." in prompt


def test_judge_turns_flags_and_counts_errors():
    async def run():
        async def ask(system, user):
            if "NEW TURN:\nThe door swings open." in user:
                return '{"contradiction": true, "reason": "canon says locked"}'
            if "NEW TURN:\nexplode" in user:
                raise RuntimeError("judge exploded")
            return '{"contradiction": false, "reason": "consistent"}'

        turns = ["You knock on the door.", "The door swings open.",
                 "explode please"]
        result = await judge_mod.judge_turns(turns, ["The door is locked."], ask)
        assert len(result["contradictions"]) == 1
        assert result["contradictions"][0].source == "judge"
        assert result["contradictions"][0].turn_index == 1
        assert result["errors"] == 1  # a failed audit is counted, not a pass
        assert len(result["verdicts"]) == 3

    import asyncio
    asyncio.run(run())


# ---------------------------------------------------------------------------
# Score integration
# ---------------------------------------------------------------------------

def _scenario(tmp_path, canon_facts=None):
    raw = {
        "id": "unit",
        "title": "unit",
        "script": [{"event": "player_message", "message": "hi"}],
        "scripted_responses": [{"actions": []}],
        "expect": {},
        "canon_facts": canon_facts or [],
    }
    path = tmp_path / "unit.json"
    path.write_text(json.dumps(raw))
    from evals.scenario import load_scenario
    return load_scenario(path)


def test_event_contradiction_fails_the_run(tmp_path):
    scenario = _scenario(tmp_path, canon_facts=[{
        "fact": "Brother Fenwick is dead; the party buried him.",
    }])
    calls = [_chat("Fenwick nods at you gravely.")]
    score = score_mod.score_run(scenario, calls, [{}], None, "live")
    assert score.contradictions
    assert score.contradictions[0].startswith("[event]")
    assert not score.passed


def test_judge_findings_join_the_contradiction_list(tmp_path):
    scenario = _scenario(tmp_path)
    judge_result = {
        "verdicts": [judge_mod.Verdict(True, "bridge burned last week")],
        "errors": 0,
        "contradictions": [
            judge_mod.Contradiction("judge", "bridge burned last week", 0)],
    }
    score = score_mod.score_run(
        scenario, [_chat("You cross the bridge.")], [{}], None, "live",
        judge_result=judge_result)
    assert score.contradictions[0].startswith("[judge]")
    assert score.judged_turns == 1
    summary = score_mod.corpus_summary([score])
    assert summary["contradictions_by_source"] == {"judge": 1}
    assert summary["contradiction_rate"] == 1.0


# ---------------------------------------------------------------------------
# Metrics: history, publish, tick gate
# ---------------------------------------------------------------------------

def _record(rate, volume=None, backend="live", sha="abc123"):
    record = {
        "recorded_at": "2026-09-04T00:00:00+00:00",
        "git_sha": sha,
        "backend": backend,
        "model": "m",
        "scenarios": 30,
        "passed": 30,
        "contradictions": rate,
        "contradiction_rate": rate,
        "by_source": {},
    }
    if volume is not None:
        record["tick_volume"] = volume
    return record


def test_history_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "history.jsonl"
    metrics_mod.append_record(_record(0.0), path)
    metrics_mod.append_record(_record(0.0333), path)
    records = metrics_mod.load_history(path)
    assert [r["contradiction_rate"] for r in records] == [0.0, 0.0333]


def test_load_history_rejects_malformed(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text('{"note": "no rate"}\n')
    with pytest.raises(ValueError):
        metrics_mod.load_history(path)


def test_tick_gate_trips_when_rate_rises_with_volume():
    records = [
        _record(0.0, volume=1),
        _record(0.01, volume=7),
        _record(0.05, volume=30),
    ]
    ok, detail = metrics_mod.check_tick_gate(records)
    assert not ok
    assert "rose with tick volume" in detail


def test_tick_gate_passes_when_rate_holds_or_falls():
    ok, _ = metrics_mod.check_tick_gate(
        [_record(0.02, volume=1), _record(0.02, volume=7),
         _record(0.0, volume=30)])
    assert ok
    # One record proves nothing, but must not block the first tick run.
    ok, detail = metrics_mod.check_tick_gate([_record(0.5, volume=1)])
    assert ok and "unproven" in detail
    ok, _ = metrics_mod.check_tick_gate([])
    assert ok


def test_publish_renders_the_number(tmp_path):
    history = tmp_path / "history.jsonl"
    metrics_mod.append_record(_record(0.0, backend="scripted"), history)
    metrics_mod.append_record(_record(0.0333, backend="live"), history)
    md = metrics_mod.render_metrics_md(metrics_mod.load_history(history))
    assert "0.0333" in md
    assert "tick gate" in md.lower()


def test_build_record_from_report():
    report = {
        "meta": {"backend": "scripted", "model": "mock-model"},
        "summary": {"scenarios": 30, "passed": 30, "contradictions": 0,
                    "contradiction_rate": 0.0,
                    "contradictions_by_source": {}},
    }
    record = metrics_mod.build_record(report, tick_volume=7)
    assert record["contradiction_rate"] == 0.0
    assert record["tick_volume"] == 7
    assert record["backend"] == "scripted"


# ---------------------------------------------------------------------------
# End-to-end: replay --record appends to the history
# ---------------------------------------------------------------------------

def test_replay_record_appends(tmp_path, monkeypatch):
    history = tmp_path / "history.jsonl"
    monkeypatch.setattr(metrics_mod, "HISTORY_PATH", history)
    rc = replay_mod.main(["--backend", "scripted", "--out", str(tmp_path),
                          "--record", "--scenario", "canon_locked_door"])
    assert rc == 0
    records = metrics_mod.load_history(history)
    assert len(records) == 1
    assert records[0]["contradiction_rate"] == 0.0
    assert records[0]["backend"] == "scripted"


def test_tick_volume_requires_record(tmp_path):
    rc = replay_mod.main(["--backend", "scripted", "--out", str(tmp_path),
                          "--tick-volume", "3"])
    assert rc == 2


def test_committed_history_is_valid():
    """The published metrics history must always parse — it's a gate input."""
    records = metrics_mod.load_history()
    for record in records:
        assert isinstance(record["contradiction_rate"], (int, float))
        assert record["backend"] in ("scripted", "live")
