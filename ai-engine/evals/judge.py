"""LLM-as-judge: catch contradictions the deterministic detectors can't.

``evals.contradictions`` is precision-first by design — it only catches what
a regex or a structured assertion can see. A model that contradicts canon in
novel phrasing ("the north bridge" when canon said the only bridge burned)
slips through. The judge closes that gap: for every GM-spoken turn it asks a
model — temperature 0, same endpoint the engine uses — whether the turn
contradicts the canon facts or the prior turns.

Design rules:

- **Reported, and gated on live runs.** A judge-flagged turn is a
  contradiction like any other and fails the run.
- **Never silently absent.** Every audited turn records a verdict; a judge
  that errors records ``judge_errors`` so a dead judge cannot greenwash a
  run — the report shows coverage, not just hits.
- **Judge output is evidence, not truth.** The verdict and its reason land in
  the run's event log (``judge_calls``) where a reviewer can read them.

Only runs invoked with ``--judge`` pay for this (one extra call per GM turn).
The scripted CI path never calls it — determinism there belongs to the
deterministic detectors.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from evals.contradictions import Contradiction

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a continuity auditor for a tabletop RPG campaign. You are given "
    "CANON (established, permanent world truth), the PRIOR TURNS of the "
    "session so far, and one NEW TURN of GM narration. Decide whether the new "
    "turn contradicts canon or the prior turns.\n\n"
    "A contradiction is a factual conflict: a dead NPC acting alive, a "
    "destroyed bridge being crossed, a locked door opening without cause, an "
    "event restated differently, a revealed secret treated as unknown. New "
    "information that does not conflict with anything is NOT a "
    "contradiction. Flavour, embellishment, and player-visible dice results "
    "are NOT contradictions. When unsure, answer false.\n\n"
    "Respond with ONLY a JSON object: "
    '{"contradiction": true|false, "reason": "one sentence"}\n'
    "No commentary before or after the JSON."
)

_MAX_PRIOR_TURNS = 12  # recency window; older context is canon-proposal work


@dataclass
class Verdict:
    contradiction: bool
    reason: str


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Tolerant JSON-object extraction (code fences, stray commentary)."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(),
                     flags=re.IGNORECASE | re.MULTILINE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


def parse_verdict(text: str) -> Optional[Verdict]:
    """Parse a judge response. None on anything unparseable — the caller
    counts it as a judge error, never as a pass."""
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    verdict = data.get("contradiction")
    if not isinstance(verdict, bool):
        return None
    reason = data.get("reason")
    return Verdict(verdict, reason.strip() if isinstance(reason, str) else "")


def build_judge_prompt(canon_facts: List[str], prior_turns: List[str],
                       turn: str) -> str:
    """The user prompt for one audited turn."""
    canon_block = ("\n".join(f"- {f}" for f in canon_facts)
                   if canon_facts else "(no canon established)")
    prior = prior_turns[-_MAX_PRIOR_TURNS:]
    prior_block = ("\n".join(f"{i + 1}. {t}" for i, t in enumerate(prior))
                   if prior else "(this is the first turn)")
    return (f"CANON:\n{canon_block}\n\n"
            f"PRIOR TURNS:\n{prior_block}\n\n"
            f"NEW TURN:\n{turn}\n\n"
            "Return the JSON now.")


async def judge_turns(turns: List[str], canon_facts: List[str],
                      ask: Callable[[str, str], "Any"],
                      ) -> Dict[str, Any]:
    """Audit every turn. ``ask(system, user)`` is one LLM call returning text
    (injectable for tests; production passes the configured endpoint).

    Returns {"verdicts": [Verdict|None per turn], "errors": int,
             "contradictions": [Contradiction]}. A None verdict is an error —
    counted, never treated as a pass.
    """
    verdicts: List[Optional[Verdict]] = []
    errors = 0
    contradictions: List[Contradiction] = []
    for i, turn in enumerate(turns):
        prompt = build_judge_prompt(canon_facts, turns[:i], turn)
        try:
            raw = await ask(_SYSTEM, prompt)
            verdict = parse_verdict(raw) if isinstance(raw, str) else None
        except Exception as exc:  # noqa: BLE001 — report, don't crash the run
            logger.warning("[judge] turn %d audit failed: %s", i, exc)
            verdict = None
        if verdict is None:
            errors += 1
        elif verdict.contradiction:
            contradictions.append(Contradiction(
                "judge", verdict.reason or "flagged by judge", i))
        verdicts.append(verdict)
    return {"verdicts": verdicts, "errors": errors,
            "contradictions": contradictions}


# ---------------------------------------------------------------------------
# Production transport: the same OpenAI-compatible endpoint the engine uses
# ---------------------------------------------------------------------------

def make_ask(model: Optional[str] = None) -> Callable[[str, str], Any]:
    """Build an ``ask`` callable against the configured LLM endpoint.

    Follows context/canon.py's pattern (direct POST, temperature 0) rather
    than LLMManager: the judge wants no history, no game-state prompt, and a
    deterministic decode.
    """
    import httpx

    from config import settings

    base = settings.llm_base_url.rstrip("/")
    endpoint = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    judge_model = model or settings.model
    client = httpx.AsyncClient(timeout=120)

    async def ask(system: str, user: str) -> str:
        payload = {
            "model": judge_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "/nothink\n" + user},
            ],
            "temperature": 0.0,
            "max_tokens": 256,
            "enable_thinking": False,
        }
        resp = await client.post(endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    ask.close = client.aclose  # type: ignore[attr-defined]
    ask.model = judge_model  # type: ignore[attr-defined]
    return ask
