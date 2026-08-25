"""Aggregate trace-level metrics (edit decisions, evidence requests, native tool
calls, edit_decision_feedback) across every run of the given experiment roots.
Read-only; never calls the model.
"""

import json
import sys
from collections import Counter
from pathlib import Path


def iter_run_dirs(root: Path):
    ws_root = root / "workspaces"
    if not ws_root.is_dir():
        return
    for ws in ws_root.iterdir():
        runs_dir = ws / ".codecub" / "runs"
        if runs_dir.is_dir():
            for run_dir in runs_dir.iterdir():
                yield run_dir


def main(argv):
    if not argv:
        print("usage: aggregate_trace_metrics.py <run-root> [<run-root>...]")
        return 1
    for arg in argv:
        root = Path(arg)
        print(f"\n=== {root.name} ===")
        totals = Counter()
        runs_seen = 0
        for run_dir in iter_run_dirs(root):
            trace_path = run_dir / "trace.jsonl"
            if not trace_path.exists():
                continue
            runs_seen += 1
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                kind = event.get("event")
                totals[kind] += 1
                if kind == "native_direct_edit_decision":
                    totals[f"edit_decision_{event.get('decision')}"] += 1
                if kind == "edit_decision_feedback":
                    totals[f"feedback_{event.get('feedback_type') or event.get('kind')}"] += 1
        keys = [
            "model_requested",
            "model_parsed",
            "native_tool_batch_queued",
            "tool_executed",
            "checkpoint_created",
            "phase_transition",
            "native_direct_edit_decision",
            "edit_decision_edit",
            "edit_decision_need_evidence",
            "edit_decision_feedback",
            "exploration_warning",
            "implementation_warning",
            "run_finished",
            "run_started",
        ]
        print(f"  runs scanned: {runs_seen}")
        for key in keys:
            if key in totals:
                print(f"  {key}: {totals[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
