"""Track the contradiction rate across builds — and gate the world tick.

"Publish the number in the repo and track it across builds" is a file, not a
dashboard: ``evals/metrics/history.jsonl`` is committed to the repo, one
record per measured run, appended by ``python -m evals.replay --record`` (or
``python -m evals.metrics append report.json``). ``publish`` regenerates
``evals/METRICS.md`` — the published number.

The tick gate (the CKP-97 acceptance criterion): the off-session world tick
(CKP-101) records runs with a ``tick_volume``; ``gate`` fails if the
contradiction rate rises with volume — CKP-101's kill criterion, enforced in
code rather than in review intentions.

CLI:

    python -m evals.metrics append <report.json> [--tick-volume N] [--note ...]
    python -m evals.metrics publish          # regenerate evals/METRICS.md
    python -m evals.metrics gate             # exit 1 if rate rises with volume
    python -m evals.metrics trend            # print the series
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

METRICS_DIR = Path(__file__).parent / "metrics"
HISTORY_PATH = METRICS_DIR / "history.jsonl"
METRICS_MD = Path(__file__).parent / "METRICS.md"

_RECORD_KEYS = {
    "recorded_at", "git_sha", "backend", "model", "scenarios", "passed",
    "contradiction_rate", "contradictions", "by_source", "tick_volume", "note",
}


def _git_sha() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).parent.parent)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 — a record without a sha still counts
        return None


def build_record(report: Dict, tick_volume: Optional[int] = None,
                 note: Optional[str] = None) -> Dict:
    """One history record from a replay report.json."""
    summary = report["summary"]
    meta = report.get("meta", {})
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "backend": meta.get("backend"),
        "model": meta.get("model"),
        "scenarios": summary.get("scenarios"),
        "passed": summary.get("passed"),
        "contradictions": summary.get("contradictions"),
        "contradiction_rate": summary.get("contradiction_rate"),
        "by_source": summary.get("contradictions_by_source", {}),
        "tick_volume": tick_volume,
        "note": note,
    }
    return {k: v for k, v in record.items() if v is not None}


def append_record(record: Dict, path: Optional[Path] = None) -> Path:
    # Resolved at call time, not def time — tests monkeypatch HISTORY_PATH.
    path = path or HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return path


def load_history(path: Optional[Path] = None) -> List[Dict]:
    path = path or HISTORY_PATH
    if not path.exists():
        return []
    records = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or "contradiction_rate" not in record:
            raise ValueError(f"{path}:{i + 1}: malformed history record")
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# The tick gate: contradiction rate must not rise with tick volume
# ---------------------------------------------------------------------------

def check_tick_gate(records: List[Dict],
                    tolerance: float = 0.0) -> Tuple[bool, str]:
    """Fail if any tick run's contradiction rate exceeds the best rate seen
    at a strictly lower tick volume (plus tolerance).

    Only records carrying ``tick_volume`` participate. With fewer than two
    such records the gate passes with an explicit "unproven" note — the gate
    binds once the tick starts recording runs, and silently vacuous gates are
    how campaigns poison their own lore.
    """
    ticked = sorted(
        (r for r in records if r.get("tick_volume") is not None),
        key=lambda r: (r["tick_volume"], r.get("recorded_at", "")))
    if len(ticked) < 2:
        return True, (f"unproven: {len(ticked)} tick record(s) — the gate "
                      "binds once runs at two or more volumes are recorded")
    for i, record in enumerate(ticked):
        lower = [r["contradiction_rate"] for r in ticked[:i]
                 if r["tick_volume"] < record["tick_volume"]]
        if not lower:
            continue
        floor = min(lower)
        if record["contradiction_rate"] > floor + tolerance:
            return False, (
                f"contradiction rate rose with tick volume: "
                f"{record['contradiction_rate']} at volume "
                f"{record['tick_volume']} vs {floor} at lower volume "
                f"(recorded {record.get('recorded_at')}, sha "
                f"{record.get('git_sha')})")
    volumes = ", ".join(str(r["tick_volume"]) for r in ticked)
    return True, f"rate held across tick volumes: {volumes}"


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

def _fmt_rate(record: Dict) -> str:
    rate = record.get("contradiction_rate")
    return f"{rate:.4f}" if isinstance(rate, (int, float)) else "n/a"


def render_metrics_md(records: List[Dict]) -> str:
    """The published number: evals/METRICS.md, regenerated by `publish`."""
    lines = [
        "# GM quality metrics",
        "",
        "The contradiction rate is the one number v2.0 optimises (CKP-97):",
        "how often a generated turn contradicts canonised fact or prior",
        "events, per scenario-run over the frozen 30-scenario corpus.",
        "Everything else is a dashboard. History: `metrics/history.jsonl`;",
        "regenerate this file with `python -m evals.metrics publish`.",
        "",
    ]
    latest: Dict[str, Dict] = {}
    for record in records:
        backend = record.get("backend") or "unknown"
        latest[backend] = record  # history is append-only → last wins
    if latest:
        lines.append("## Current")
        lines.append("")
        lines.append("| Backend | Model | Contradiction rate | Pass | Build | Recorded |")
        lines.append("|---|---|---|---|---|---|")
        for backend, record in sorted(latest.items()):
            passed = f"{record.get('passed')}/{record.get('scenarios')}"
            lines.append(
                f"| {backend} | {record.get('model', 'n/a')} | "
                f"**{_fmt_rate(record)}** | {passed} | "
                f"`{record.get('git_sha', 'n/a')}` | "
                f"{record.get('recorded_at', 'n/a')} |")
        lines.append("")
    ticked = [r for r in records if r.get("tick_volume") is not None]
    ok, detail = check_tick_gate(records)
    lines += [
        "## Off-session tick gate",
        "",
        f"Gate: **{'PASS' if ok else 'FAIL'}** — {detail}.",
        "",
    ]
    if ticked:
        lines += [
            "| Tick volume | Contradiction rate | Build | Recorded |",
            "|---|---|---|---|",
        ]
        for record in sorted(ticked, key=lambda r: r["tick_volume"]):
            lines.append(
                f"| {record['tick_volume']} | {_fmt_rate(record)} | "
                f"`{record.get('git_sha', 'n/a')}` | "
                f"{record.get('recorded_at', 'n/a')} |")
        lines.append("")
    lines += [
        "## History",
        "",
        "| Recorded | Backend | Model | Rate | By source | Pass | Build |",
        "|---|---|---|---|---|---|---|",
    ]
    for record in records:
        by_source = ", ".join(f"{k}:{v}" for k, v in
                              sorted((record.get("by_source") or {}).items()))
        passed = f"{record.get('passed')}/{record.get('scenarios')}"
        lines.append(
            f"| {record.get('recorded_at', 'n/a')} | "
            f"{record.get('backend', 'n/a')} | {record.get('model', 'n/a')} | "
            f"{_fmt_rate(record)} | {by_source or '—'} | {passed} | "
            f"`{record.get('git_sha', 'n/a')}` |")
    if not records:
        lines.append("| — | — | — | — | — | — | — |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.metrics")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_append = sub.add_parser("append", help="record a replay report.json")
    p_append.add_argument("report", type=Path)
    p_append.add_argument("--tick-volume", type=int, default=None)
    p_append.add_argument("--note", default=None)
    p_append.add_argument("--history", type=Path, default=HISTORY_PATH)
    p_pub = sub.add_parser("publish", help="regenerate evals/METRICS.md")
    p_pub.add_argument("--history", type=Path, default=HISTORY_PATH)
    p_gate = sub.add_parser("gate", help="exit 1 if rate rises with tick volume")
    p_gate.add_argument("--history", type=Path, default=HISTORY_PATH)
    p_gate.add_argument("--tolerance", type=float, default=0.0)
    p_trend = sub.add_parser("trend", help="print the recorded series")
    p_trend.add_argument("--history", type=Path, default=HISTORY_PATH)
    args = parser.parse_args(argv)

    if args.cmd == "append":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        record = build_record(report, tick_volume=args.tick_volume,
                              note=args.note)
        path = append_record(record, args.history)
        print(f"[metrics] recorded rate {_fmt_rate(record)} → {path}")
        return 0

    if args.cmd == "publish":
        records = load_history(args.history)
        METRICS_MD.write_text(render_metrics_md(records), encoding="utf-8")
        print(f"[metrics] published {len(records)} record(s) → {METRICS_MD}")
        return 0

    if args.cmd == "gate":
        ok, detail = check_tick_gate(load_history(args.history),
                                     tolerance=args.tolerance)
        print(f"[metrics] tick gate {'PASS' if ok else 'FAIL'}: {detail}")
        return 0 if ok else 1

    if args.cmd == "trend":
        for record in load_history(args.history):
            volume = (f" tick_volume={record['tick_volume']}"
                      if record.get("tick_volume") is not None else "")
            print(f"{record.get('recorded_at', 'n/a')}  "
                  f"{record.get('backend', 'n/a'):9s}  "
                  f"rate={_fmt_rate(record)}{volume}  "
                  f"sha={record.get('git_sha', 'n/a')}")
        return 0

    return 2  # unreachable; subparsers are required


if __name__ == "__main__":
    sys.exit(main())
