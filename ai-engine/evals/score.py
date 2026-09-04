"""Score a scenario replay: hard checks, contradiction scan, baseline drift.

Three families of measurement, one report:

- **Hard checks** (``expect`` block) — deterministic gates: the run must
  narrate, must not start combat unprompted, must stay within a call budget.
  A hard-check failure means the run is broken, not merely different.
- **Contradictions** — the project's north-star metric. Each scenario may
  declare ``canon_facts`` with ``contradiction_patterns`` (regexes); every
  pattern hit in GM-spoken text is one contradiction. The report aggregates
  these into the corpus contradiction rate.
- **Drift** — soft similarity between the fresh run's Foundry-call sequence
  and the frozen baseline's (SequenceMatcher ratio over method names, plus
  narrated-text keyword overlap). Drift is information, not a gate: a
  better-but-different run drifts too, which is why a human reviews the diff.
"""

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Foundry-call methods that represent the GM saying something. Contradiction
# and mention scans only look at these — a setup_scene call can't contradict
# canon, only words can.
_SPOKEN_METHODS = {"chat_message", "whisper"}

# Calls the engine makes while preparing a turn rather than as GM output.
# Excluded from the drift sequence so housekeeping doesn't drown the signal.
_HOUSEKEEPING_METHODS = {
    "subscribe_to_channel", "get_actors", "get_scene_tokens",
    "list_scene_names", "scan_world", "execute_js",
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ScenarioScore:
    scenario_id: str
    backend: str
    checks: List[CheckResult] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)
    drift: Optional[float] = None          # 1.0 == identical to baseline
    text_overlap: Optional[float] = None   # keyword overlap vs baseline, 0..1
    llm_calls: int = 0
    foundry_calls: int = 0
    error: Optional[str] = None

    @property
    def hard_failures(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.ok]

    @property
    def passed(self) -> bool:
        return self.error is None and not self.hard_failures and not self.contradictions


def spoken_texts(foundry_calls: List[Dict]) -> List[str]:
    """Every string the GM said out loud, in order."""
    return [c.get("text", "") for c in foundry_calls
            if c.get("method") in _SPOKEN_METHODS and c.get("text")]


def action_sequence(foundry_calls: List[Dict]) -> List[str]:
    """The GM's output-call sequence, minus housekeeping round-trips."""
    return [c["method"] for c in foundry_calls
            if c.get("method") not in _HOUSEKEEPING_METHODS]


def _keywords(texts: List[str]) -> set:
    words = set()
    for text in texts:
        words.update(w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 3)
    return words


def check_expectations(scenario, foundry_calls: List[Dict],
                       llm_call_count: int) -> List[CheckResult]:
    """Evaluate the scenario's ``expect`` block against a recorded run."""
    expect = scenario.expect
    checks: List[CheckResult] = []
    spoken = " ".join(spoken_texts(foundry_calls)).lower()

    def _matches(spec: str) -> List[Dict]:
        """Calls matching 'method' or 'method:key=value' (all constraints)."""
        method, _, constraint = spec.partition(":")
        out = [c for c in foundry_calls if c.get("method") == method]
        if constraint:
            key, _, value = constraint.partition("=")
            out = [c for c in out if str(c.get(key)) == value]
        return out

    for spec in expect.get("must_call", []):
        hits = _matches(spec)
        checks.append(CheckResult(
            f"must_call:{spec}", bool(hits),
            "" if hits else f"{spec} never called"))
    for spec in expect.get("must_not_call", []):
        hits = _matches(spec)
        checks.append(CheckResult(
            f"must_not_call:{spec}", not hits,
            "" if not hits else f"{spec} called {len(hits)}x"))
    for entry in expect.get("must_mention", []):
        # An entry may be a list of alternatives — any one satisfies it.
        alternatives = entry if isinstance(entry, list) else [entry]
        hit = next((w for w in alternatives if w.lower() in spoken), None)
        checks.append(CheckResult(
            f"must_mention:{'|'.join(alternatives)}", hit is not None,
            "" if hit else "never mentioned"))
    for word in expect.get("must_not_mention", []):
        checks.append(CheckResult(
            f"must_not_mention:{word}", word.lower() not in spoken,
            "" if word.lower() not in spoken else "mentioned"))

    min_calls = expect.get("min_llm_calls")
    if min_calls is not None:
        checks.append(CheckResult(
            "min_llm_calls", llm_call_count >= min_calls,
            f"{llm_call_count} < {min_calls}" if llm_call_count < min_calls else ""))
    max_calls = expect.get("max_llm_calls")
    if max_calls is not None:
        checks.append(CheckResult(
            "max_llm_calls", llm_call_count <= max_calls,
            f"{llm_call_count} > {max_calls}" if llm_call_count > max_calls else ""))
    return checks


def scan_contradictions(scenario, foundry_calls: List[Dict]) -> List[str]:
    """Regex each canon fact's contradiction patterns over GM-spoken text."""
    hits: List[str] = []
    for text in spoken_texts(foundry_calls):
        for cf in scenario.canon_facts:
            for pattern in cf.contradiction_patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    hits.append(f"canon {cf.fact!r} contradicted by {m.group(0)!r}")
    return hits


def compute_drift(baseline_calls: List[Dict],
                  fresh_calls: List[Dict]) -> tuple:
    """(action-sequence ratio, spoken-text keyword overlap) vs the baseline.

    Returns (None, None) when no baseline exists yet.
    """
    if baseline_calls is None:
        return None, None
    seq_ratio = difflib.SequenceMatcher(
        None,
        action_sequence(baseline_calls),
        action_sequence(fresh_calls),
    ).ratio()
    base_words = _keywords(spoken_texts(baseline_calls))
    fresh_words = _keywords(spoken_texts(fresh_calls))
    if not base_words or not fresh_words:
        return seq_ratio, 0.0
    overlap = len(base_words & fresh_words) / len(base_words | fresh_words)
    return round(seq_ratio, 3), round(overlap, 3)


def score_run(scenario, foundry_calls: List[Dict], llm_calls: List[Dict],
              baseline_calls: Optional[List[Dict]], backend: str,
              error: Optional[str] = None) -> ScenarioScore:
    score = ScenarioScore(
        scenario_id=scenario.id, backend=backend,
        llm_calls=len(llm_calls), foundry_calls=len(foundry_calls),
        error=error,
    )
    if error:
        return score
    score.checks = check_expectations(scenario, foundry_calls, len(llm_calls))
    score.contradictions = scan_contradictions(scenario, foundry_calls)
    score.drift, score.text_overlap = compute_drift(baseline_calls, foundry_calls)
    return score


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def corpus_summary(scores: List[ScenarioScore]) -> Dict:
    """Aggregate per-scenario scores into corpus-level metrics.

    Contradiction rate is contradictions per scenario-run — the number the
    v2.0 release criteria tracks trending down.
    """
    total = len(scores)
    contradictions = sum(len(s.contradictions) for s in scores)
    drifts = [s.drift for s in scores if s.drift is not None]
    return {
        "scenarios": total,
        "passed": sum(1 for s in scores if s.passed),
        "failed": sum(1 for s in scores if not s.passed),
        "hard_check_failures": sum(len(s.hard_failures) for s in scores),
        "contradictions": contradictions,
        "contradiction_rate": round(contradictions / total, 4) if total else 0.0,
        "mean_drift": round(sum(drifts) / len(drifts), 4) if drifts else None,
    }


def report_json(scores: List[ScenarioScore], meta: Dict) -> Dict:
    return {
        "meta": meta,
        "summary": corpus_summary(scores),
        "scenarios": [
            {
                "id": s.scenario_id,
                "passed": s.passed,
                "error": s.error,
                "llm_calls": s.llm_calls,
                "foundry_calls": s.foundry_calls,
                "drift": s.drift,
                "text_overlap": s.text_overlap,
                "contradictions": s.contradictions,
                "checks": [
                    {"name": c.name, "ok": c.ok, "detail": c.detail}
                    for c in s.checks
                ],
            }
            for s in scores
        ],
    }


def report_markdown(scores: List[ScenarioScore], meta: Dict) -> str:
    summary = corpus_summary(scores)
    lines = [
        "# Eval replay report",
        "",
        f"- backend: `{meta.get('backend')}`  model: `{meta.get('model', 'n/a')}`",
        f"- generated: {meta.get('generated_at')}",
        f"- scenarios: **{summary['passed']}/{summary['scenarios']} passed**",
        f"- hard-check failures: **{summary['hard_check_failures']}**",
        f"- contradictions: **{summary['contradictions']}** "
        f"(rate {summary['contradiction_rate']} per scenario)",
    ]
    if summary["mean_drift"] is not None:
        lines.append(f"- mean action-sequence drift vs baseline: "
                     f"**{summary['mean_drift']}** (1.0 = identical)")
    lines += [
        "",
        "| Scenario | Result | Drift | Contradictions | Failed checks |",
        "|---|---|---|---|---|",
    ]
    for s in scores:
        result = "PASS" if s.passed else ("ERROR" if s.error else "FAIL")
        drift = f"{s.drift:.2f}" if s.drift is not None else "—"
        failed = "; ".join(c.name for c in s.hard_failures) or "—"
        if s.error:
            failed = s.error
        lines.append(
            f"| {s.scenario_id} | {result} | {drift} | "
            f"{len(s.contradictions) or '—'} | {failed} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(scores: List[ScenarioScore], meta: Dict,
                  out_dir: Path) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "report.json",
        "markdown": out_dir / "report.md",
    }
    paths["json"].write_text(
        json.dumps(report_json(scores, meta), indent=2) + "\n", encoding="utf-8")
    paths["markdown"].write_text(report_markdown(scores, meta), encoding="utf-8")
    return paths
