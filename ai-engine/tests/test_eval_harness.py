"""Tests for the eval replay harness itself (scripted backend — no live model).

These guard the machinery that guards the GM: corpus integrity, freeze/replay
determinism, drift and contradiction scoring, and report structure.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import replay as replay_mod
from evals import score as score_mod
from evals.harness import RecordingLLM, ScriptedLLM
from evals.scenario import load_corpus, load_scenario


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------

def test_corpus_loads_30_scenarios():
    corpus = load_corpus()
    assert len(corpus) == 30
    ids = [s.id for s in corpus]
    assert len(set(ids)) == 30, "scenario ids must be unique"


def test_every_scenario_has_baseline_and_scripted_responses():
    for s in load_corpus():
        assert s.scripted_responses, f"{s.id}: missing scripted_responses"
        assert s.baseline_path.exists(), f"{s.id}: no frozen baseline (run --freeze)"
        baseline = json.loads(s.baseline_path.read_text())
        assert baseline["scenario"] == s.id
        assert baseline["foundry_calls"], f"{s.id}: baseline has no foundry calls"


# ---------------------------------------------------------------------------
# Full scripted replay (deterministic — the CI gate for harness regressions)
# ---------------------------------------------------------------------------

def test_scripted_replay_of_corpus_is_green(tmp_path):
    rc = replay_mod.main(["--backend", "scripted", "--out", str(tmp_path)])
    assert rc == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["summary"]["scenarios"] == 30
    assert report["summary"]["failed"] == 0
    assert report["summary"]["contradiction_rate"] == 0.0
    # Scripted replay reproduces the frozen baseline exactly.
    assert report["summary"]["mean_drift"] == 1.0
    assert (tmp_path / "report.md").exists()


# ---------------------------------------------------------------------------
# Scoring units
# ---------------------------------------------------------------------------

def _scenario(tmp_path, expect=None, canon_facts=None):
    raw = {
        "id": "unit",
        "title": "unit",
        "script": [{"event": "player_message", "message": "hi"}],
        "scripted_responses": [{"actions": []}],
        "expect": expect or {},
        "canon_facts": canon_facts or [],
    }
    path = tmp_path / "unit.json"
    path.write_text(json.dumps(raw))
    return load_scenario(path)


def test_drift_identical_vs_different(tmp_path):
    baseline = [
        {"method": "chat_message", "text": "The goblin eyes you warily."},
        {"method": "start_combat", "token_ids": ["t1", "t2"]},
    ]
    same = score_mod.compute_drift(baseline, baseline)
    assert same[0] == 1.0

    different = [
        {"method": "chat_message", "text": "A dragon descends from the ceiling."},
    ]
    ratio, overlap = score_mod.compute_drift(baseline, different)
    assert ratio < 1.0
    assert overlap < 1.0


def test_arg_qualified_must_not_call(tmp_path):
    scenario = _scenario(tmp_path, expect={
        "must_not_call": ["roll:speaker=Aria", "roll:speaker=Goblin"],
    })
    calls = [{"method": "roll", "formula": "1d20", "speaker": "Aria"}]
    checks = score_mod.check_expectations(scenario, calls, 1)
    by_name = {c.name: c for c in checks}
    assert by_name["must_not_call:roll:speaker=Aria"].ok is False
    assert by_name["must_not_call:roll:speaker=Goblin"].ok is True


def test_must_mention_alternatives(tmp_path):
    scenario = _scenario(tmp_path, expect={
        "must_mention": [["flee", "bolts", "runs"]],
    })
    calls = [{"method": "chat_message", "text": "He bolts for the archway."}]
    checks = score_mod.check_expectations(scenario, calls, 1)
    assert checks[0].ok is True

    calls = [{"method": "chat_message", "text": "He stands his ground."}]
    checks = score_mod.check_expectations(scenario, calls, 1)
    assert checks[0].ok is False


def test_contradiction_scan_flags_canon_violation(tmp_path):
    scenario = _scenario(tmp_path, canon_facts=[{
        "fact": "The innkeeper is named Borin.",
        "contradiction_patterns": ["name is (?!Borin)\\w+"],
    }])
    bad = [{"method": "chat_message", "text": "My name is Gronk, traveller."}]
    assert score_mod.scan_contradictions(scenario, bad)
    good = [{"method": "chat_message", "text": "My name is Borin, traveller."}]
    assert not score_mod.scan_contradictions(scenario, good)
    # Housekeeping calls are never scanned for contradictions.
    neutral = [{"method": "execute_js", "code": "name is Gronk"}]
    assert not score_mod.scan_contradictions(scenario, neutral)


def test_report_contains_corpus_metrics(tmp_path):
    scenario = _scenario(tmp_path, expect={"must_call": ["chat_message"]})
    scores = [
        score_mod.score_run(scenario,
                            [{"method": "chat_message", "text": "hello"}],
                            [{}], None, "scripted"),
    ]
    paths = score_mod.write_reports(
        scores, {"backend": "scripted", "model": "m", "generated_at": "now"}, tmp_path)
    report = json.loads(paths["json"].read_text())
    assert report["summary"]["passed"] == 1
    assert report["summary"]["contradiction_rate"] == 0.0
    assert "contradictions:" in paths["markdown"].read_text()


# ---------------------------------------------------------------------------
# RecordingLLM (the live backend's transcript capture)
# ---------------------------------------------------------------------------

def test_recording_llm_captures_exchange():
    async def run():
        inner = ScriptedLLM([{"actions": [{"type": "narrate", "text": "hi"}]}])
        rec = RecordingLLM(inner)
        resp = await rec.generate("hello", game_state_summary="s", extra_context="e")
        assert resp["actions"][0]["type"] == "narrate"
        assert len(rec.calls) == 1
        call = rec.calls[0]
        assert call["user_message"] == "hello"
        assert call["game_state_summary"] == "s"
        assert call["model"] == "mock-model"
        assert "latency_s" in call
        # Attribute delegation: things like system_prompt reach the inner LLM.
        assert rec.system_prompt == inner.system_prompt

    import asyncio
    asyncio.run(run())
