"""Scenario corpus: load and validate frozen replay scenarios.

A scenario is one JSON file in ``evals/scenarios/``:

.. code-block:: json

    {
      "id": "goblin_ambush",
      "title": "Player wakes the goblin sentry",
      "tags": ["combat", "ambush"],
      "setup": {
        "campaign": "The Shattered Oath",
        "scene": "The Sunken Crypt",
        "mode": "exploration",
        "actors":  [ ... MockFoundryClient actor fixtures ... ],
        "tokens":  [ ... scene-token fixtures ... ],
        "scenes":  [ ... scene names ... ],
        "npcs":    {"Goblin": {"description": "...", "personality_traits": "..."}},
        "llm_context": {"canon": "...", "world": "...", "npcs": "...", "house_rules": "..."},
        "history": [{"role": "user", "content": "..."},
                    {"role": "assistant", "content": "{\\"actions\\": [...]}"}]
      },
      "canon_facts": [
        {"fact": "The innkeeper is named Borin.",
         "contradiction_patterns": ["innkeeper (is |named )?(?!Borin)"]}
      ],
      "script": [
        {"event": "session_start"},
        {"event": "player_message", "speaker": "Aria", "message": "I look around."},
        {"event": "idle"},
        {"event": "hook", "hook": "pauseGame", "data": {"paused": true}}
      ],
      "scripted_responses": [
        {"actions": [{"type": "narrate", "text": "..."}]}
      ],
      "expect": {
        "must_call": ["chat_message"],
        "must_not_call": ["start_combat"],
        "must_mention": ["goblin"],
        "must_not_mention": ["dragon"],
        "min_llm_calls": 1,
        "max_llm_calls": 3
      }
    }

``scripted_responses`` is the human-authored golden run: it defines what a
good GM does in this scene and produces the frozen baseline event log
(``evals/baselines/<id>.events.json``) via ``replay.py --freeze``. The live
backend ignores it and asks the real model instead.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
BASELINES_DIR = Path(__file__).parent / "baselines"

_VALID_EVENTS = {"session_start", "player_message", "idle", "pacing", "hook"}
_EXPECT_KEYS = {
    "must_call", "must_not_call", "must_mention", "must_not_mention",
    "min_llm_calls", "max_llm_calls",
}


class ScenarioError(ValueError):
    """Raised when a scenario file fails validation."""


@dataclass
class CanonFact:
    fact: str
    contradiction_patterns: List[str] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    title: str
    tags: List[str]
    setup: Dict
    canon_facts: List[CanonFact]
    script: List[Dict]
    scripted_responses: List[Dict]
    expect: Dict
    path: Optional[Path] = None

    @property
    def baseline_path(self) -> Path:
        return BASELINES_DIR / f"{self.id}.events.json"


def _validate(scenario_id: str, raw: Dict) -> None:
    def fail(msg):
        raise ScenarioError(f"{scenario_id}: {msg}")

    if not isinstance(raw.get("title"), str) or not raw["title"].strip():
        fail("missing 'title'")
    script = raw.get("script")
    if not isinstance(script, list) or not script:
        fail("'script' must be a non-empty list")
    for i, step in enumerate(script):
        event = step.get("event")
        if event not in _VALID_EVENTS:
            fail(f"script[{i}]: unknown event {event!r} (valid: {sorted(_VALID_EVENTS)})")
        if event == "player_message" and not step.get("message"):
            fail(f"script[{i}]: player_message requires 'message'")
    responses = raw.get("scripted_responses", [])
    if not isinstance(responses, list):
        fail("'scripted_responses' must be a list")
    for i, resp in enumerate(responses):
        if not isinstance(resp.get("actions"), list):
            fail(f"scripted_responses[{i}]: missing 'actions' list")
    llm_events = sum(
        1 for s in script
        if s["event"] in ("session_start", "player_message", "idle", "pacing")
        and not s.get("dropped")
    )
    if responses and len(responses) < llm_events:
        fail(f"scripted_responses has {len(responses)} entries but the script can "
             f"trigger {llm_events} LLM calls — later beats would repeat the last response")
    expect = raw.get("expect", {})
    unknown = set(expect) - _EXPECT_KEYS
    if unknown:
        fail(f"unknown expect keys: {sorted(unknown)} (valid: {sorted(_EXPECT_KEYS)})")
    for i, cf in enumerate(raw.get("canon_facts", [])):
        if not cf.get("fact"):
            fail(f"canon_facts[{i}]: missing 'fact'")
        for pattern in cf.get("contradiction_patterns", []):
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                fail(f"canon_facts[{i}]: bad contradiction pattern {pattern!r}: {exc}")


def load_scenario(path: Path) -> Scenario:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scenario_id = raw.get("id") or path.stem
    _validate(scenario_id, raw)
    return Scenario(
        id=scenario_id,
        title=raw["title"],
        tags=list(raw.get("tags", [])),
        setup=dict(raw.get("setup", {})),
        canon_facts=[
            CanonFact(fact=cf["fact"],
                      contradiction_patterns=list(cf.get("contradiction_patterns", [])))
            for cf in raw.get("canon_facts", [])
        ],
        script=list(raw["script"]),
        scripted_responses=list(raw.get("scripted_responses", [])),
        expect=dict(raw.get("expect", {})),
        path=path,
    )


def load_corpus(corpus_dir: Optional[Path] = None,
                only: Optional[List[str]] = None) -> List[Scenario]:
    """Load every scenario in the corpus, sorted by id for stable ordering."""
    directory = corpus_dir or SCENARIOS_DIR
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ScenarioError(f"no scenario files found in {directory}")
    scenarios = [load_scenario(p) for p in paths]
    if only:
        wanted = set(only)
        scenarios = [s for s in scenarios if s.id in wanted]
        missing = wanted - {s.id for s in scenarios}
        if missing:
            raise ScenarioError(f"unknown scenario id(s): {sorted(missing)}")
    return scenarios
