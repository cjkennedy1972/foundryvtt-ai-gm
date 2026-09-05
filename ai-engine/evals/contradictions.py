"""Contradiction detection — the one metric v2.0 optimises.

Does turn N contradict a canonised fact or a prior event? We own both ground
truths: the canon store and the event log. This module is the single home for
that question, used by three callers:

- the replay harness (``evals.score`` / ``evals.replay``) — per-scenario and
  corpus contradiction rates over the frozen corpus;
- the LLM judge (``evals.judge``) — the general, novel-phrasing case;
- the off-session world tick (CKP-101) — ``scan_turns`` audits each tick's
  output before it may be delivered, and the metrics gate
  (``evals.metrics --gate``) enforces "rate must not rise with tick volume".

Two deterministic detectors live here:

- **canon-pattern scan** — scenario-authored ``contradiction_patterns``
  regexed over GM-spoken text. Highest precision; only catches what an author
  predicted.
- **event-log vitality scan** — structured assertions extracted from canon
  fact *text* ("Brother Fenwick is dead…") applied to the run's own event
  log: a dead NPC may not speak (``speaker=`` on a call) and may not be
  narrated as acting alive. No per-scenario authoring required — the
  generalisation of the canon traps.

Both are precision-first: a false positive fails a good run and erodes trust
in the gate, a false negative is caught by the judge or the next corpus
scenario. Novel phrasing beyond these detectors is the judge's job.
"""

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

# Foundry-call methods that represent the GM saying something. Only words can
# contradict canon — a setup_scene or roll call can't.
SPOKEN_METHODS = {"chat_message", "whisper"}


@dataclass
class Contradiction:
    """One detected contradiction. ``source`` is "canon" (authored pattern),
    "event" (event-log/vitality detector), or "judge" (LLM auditor)."""
    source: str
    detail: str
    turn_index: Optional[int] = None

    def __str__(self) -> str:  # report entries stay plain strings
        where = f" turn {self.turn_index}:" if self.turn_index is not None else ""
        return f"[{self.source}]{where} {self.detail}"


def spoken_turns(foundry_calls: List[Dict]) -> List[str]:
    """Every string the GM said out loud, in order."""
    return [c.get("text", "") for c in foundry_calls
            if c.get("method") in SPOKEN_METHODS and c.get("text")]


# ---------------------------------------------------------------------------
# Detector 1: scenario-authored canon patterns
# ---------------------------------------------------------------------------

def scan_canon_patterns(canon_facts: Iterable,
                        foundry_calls: List[Dict]) -> List[Contradiction]:
    """Regex each canon fact's contradiction patterns over GM-spoken text."""
    hits: List[Contradiction] = []
    for turn_index, text in enumerate(spoken_turns(foundry_calls)):
        for cf in canon_facts:
            for pattern in getattr(cf, "contradiction_patterns", []):
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    hits.append(Contradiction(
                        "canon",
                        f"canon {cf.fact!r} contradicted by {m.group(0)!r}",
                        turn_index))
    return hits


# ---------------------------------------------------------------------------
# Detector 2: event-log vitality (dead things stay dead)
# ---------------------------------------------------------------------------

# "Brother Fenwick is dead.", "Marta was slain by ghouls.", "The bridge is
# destroyed" is a place, not vitality — persons only here. Conservative: the
# name must immediately precede the death copula, and we take the longest
# capitalised run as the entity.
_DEAD_COPULA = re.compile(
    r"([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)"
    r"(?:\s+(?:the|a|an)\s+[\w'-]+)?"  # "Marta the smith was slain…"
    r"\s+(?:is|was|lies|remains|now lies)\s+(?:now\s+)?"
    r"(?:dead|slain|killed|deceased|murdered)\b")
# "…ghouls killed Brother Fenwick three days ago", "the party buried Fenwick".
_DEAD_PASSIVE = re.compile(
    r"(?:killed|slew|buried|murdered|executed)\s+"
    r"([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)")

# A dead entity narrated as acting alive: name followed closely by an
# action/state verb of the living. Window is short and the verb list narrow —
# precision first. "Fenwick's grave has settled" matches nothing; "Fenwick
# nods" does.
_ALIVE_ACT = (
    r"\b(?:says|asks|replies|answers|nods|shakes|smiles|frowns|laughs|sighs|"
    r"walks|enters|leaves|emerges|appears|greets|approaches|steps|stands|"
    r"sits|waits|speaks|whispers|shouts|calls|attacks|charges|draws|offers|"
    r"hands|gives|takes|looks|stares|gestures|points|moves|turns|breathes|"
    r"waves|bows|kneels|climbs|runs|fights|follows|leads|serves|pours)\b"
)
# …but not when the mention is plainly a remnant: "the ghost of Fenwick",
# "Fenwick's grave", "a statue of Fenwick".
_REMNANT = re.compile(
    r"(?:ghost|spirit|shade|memory|portrait|statue|grave|corpse|body|tomb|"
    r"shrine|effigy|likeness)\s+of\s+$", re.IGNORECASE)


def extract_dead_entities(facts: Iterable[str]) -> List[str]:
    """Pull "X is dead" assertions out of canon fact text.

    Returns entity names, longest first (so "Brother Fenwick" matches before
    "Fenwick"). This is the structured ground truth the event-log detector
    enforces without any per-scenario authoring.
    """
    names = set()
    for fact in facts:
        for m in _DEAD_COPULA.finditer(fact):
            names.add(m.group(1).strip())
        for m in _DEAD_PASSIVE.finditer(fact):
            names.add(m.group(1).strip())
    # Drop names that are suffixes of a longer name ("Fenwick" when "Brother
    # Fenwick" is present) — matching longest-first handles overlap anyway.
    return sorted(names, key=len, reverse=True)


def _name_pattern(name: str) -> str:
    """Match the full name or its last word ("Brother Fenwick" → both)."""
    parts = name.split()
    alternatives = [re.escape(name)]
    if len(parts) > 1:
        alternatives.append(re.escape(parts[-1]))
    return rf"(?:{'|'.join(alternatives)})"


def scan_event_log(foundry_calls: List[Dict],
                   canon_facts: Iterable[str]) -> List[Contradiction]:
    """Enforce vitality assertions from canon against the run's event log.

    Two checks, both derived from the recorded run itself (the event log):
    - **dead speaker** — a ``chat_message``/``whisper`` attributed to an
      entity canon says is dead. Fully structured; cannot false-positive on
      prose style.
    - **dead acts** — GM narration has the dead entity performing a
      living action. Lexical, precision-first, remnant-mentions excluded.
    """
    dead = extract_dead_entities(canon_facts)
    if not dead:
        return []
    hits: List[Contradiction] = []

    # Structured: who spoke. Speaker names come from the action dispatcher,
    # not prose, so exact containment on word boundaries is enough.
    for call in foundry_calls:
        if call.get("method") not in SPOKEN_METHODS:
            continue
        speaker = str(call.get("speaker") or "")
        if not speaker:
            continue
        for name in dead:
            if re.search(rf"\b{_name_pattern(name)}\b", speaker):
                hits.append(Contradiction(
                    "event",
                    f"{speaker!r} speaks, but canon says {name!r} is dead"))
                break

    # Lexical: narration has the dead entity acting alive.
    verb_pat = re.compile(_ALIVE_ACT, re.IGNORECASE)
    turn_index = -1
    for call in foundry_calls:
        if call.get("method") not in SPOKEN_METHODS or not call.get("text"):
            continue
        turn_index += 1
        text = call["text"]
        for name in dead:
            name_pat = re.compile(rf"\b{_name_pattern(name)}\b", re.IGNORECASE)
            for nm in name_pat.finditer(text):
                if text[nm.end():nm.end() + 2] == "'s":
                    continue  # possessive: "Fenwick's grave" — about a remnant
                preceding = text[max(0, nm.start() - 40):nm.start()]
                if _REMNANT.search(preceding):
                    continue  # "the ghost of Fenwick", "a statue of Fenwick"
                window = re.split(r"[.?!]", text[nm.end():nm.end() + 41])[0]
                vm = verb_pat.search(window)
                if vm:
                    hits.append(Contradiction(
                        "event",
                        f"{name!r} is dead per canon, but narrated acting "
                        f"({nm.group(0)!r} … {vm.group(0)!r})", turn_index))
    return hits


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def scan_run(canon_facts, foundry_calls: List[Dict]) -> List[Contradiction]:
    """Every deterministic contradiction in one scenario run."""
    fact_texts = [cf.fact for cf in canon_facts]
    return (scan_canon_patterns(canon_facts, foundry_calls)
            + scan_event_log(foundry_calls, fact_texts))


def scan_turns(turns: List[str], canon_facts: List[str],
               prior_events: Optional[List[str]] = None) -> List[Contradiction]:
    """Audit a batch of GM-spoken turns against ground truth — the tick API.

    Source-agnostic: ``turns`` are plain strings (narration the tick wants to
    deliver), ``canon_facts`` and ``prior_events`` are plain fact/event text.
    The off-session tick calls this before delivering its output; the LLM
    judge (evals.judge) covers what structured extraction cannot see.

    Deterministic coverage here: vitality assertions from canon AND from
    prior events (an event that says "the caravan reached Arnholm yesterday"
    is ground truth for subsequent turns the same way canon is).
    """
    ground_truth = list(canon_facts) + list(prior_events or [])
    calls = [{"method": "chat_message", "text": t, "speaker": ""}
             for t in turns]
    return scan_event_log(calls, ground_truth)
