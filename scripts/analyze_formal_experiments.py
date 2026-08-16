"""Read-only analysis of formal experiment artifacts for the final report.

Computes the metrics required by the formal experiment plan from the
manifest.json / runs.jsonl / summary.json files.  Never calls the model and
never modifies any artifact.  Usage:

    python scripts/analyze_formal_experiments.py <experiment-root-dir> [<more dirs>...]

Each directory must be the run root (the directory that contains runs.jsonl).
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


def load_rows(root: Path):
    rows = []
    runs_path = root / "runs.jsonl"
    if not runs_path.exists():
        return rows
    for line in runs_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def num(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return mean(values) if values else None


def med(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return median(values) if values else None


def pct(n, d):
    return None if not d else n / d * 100


def reduction(off, on):
    if not isinstance(off, (int, float)) or not off:
        return None
    return (off - on) / off * 100


def summarize_rows(rows, label):
    completed = [r for r in rows if r.get("status") in {"pass", "fail"}]
    n = len(completed)
    passed = sum(bool(r.get("passed")) for r in completed)
    verifier = sum(bool(r.get("verifier_passed")) for r in completed)
    within = sum(bool(r.get("within_budget")) for r in completed)
    out = {
        "label": label,
        "total_runs": len(rows),
        "completed": n,
        "passed": passed,
        "pass_rate": pct(passed, n),
        "verifier_pass_rate": pct(verifier, n),
        "within_budget_rate": pct(within, n),
        "infrastructure_errors": sum(
            r.get("status") == "infrastructure_error" for r in rows
        ),
        "mean_tool_steps": num([r.get("tool_steps") for r in completed]),
        "median_tool_steps": med([r.get("tool_steps") for r in completed]),
        "mean_input_tokens": num([r.get("input_tokens") for r in completed]),
        "median_input_tokens": med([r.get("input_tokens") for r in completed]),
        "mean_output_tokens": num([r.get("output_tokens") for r in completed]),
        "mean_duration_ms": num([r.get("duration_ms") for r in completed]),
        "mean_read_calls": num([r.get("read_calls") for r in completed]),
        "mean_unique_read_files": num([r.get("unique_read_files") for r in completed]),
        "mean_repeated_read_calls": num(
            [r.get("repeated_read_calls") for r in completed]
        ),
        "mean_search_calls": num([r.get("search_calls") for r in completed]),
        "mean_patch_calls": num([r.get("patch_calls") for r in completed]),
        "mean_workspace_change_count": num(
            [r.get("workspace_change_count") for r in completed]
        ),
        "mean_first_action_step": num([r.get("first_action_step") for r in completed]),
        "mean_first_verification_after_change_step": num(
            [r.get("first_verification_after_change_step") for r in completed]
        ),
        "mean_context_reduction_count": num(
            [r.get("context_reduction_count") for r in completed]
        ),
        "mean_memory_recall_count": num(
            [r.get("memory_recall_count") for r in completed]
        ),
        "mean_file_summary_recall_count": num(
            [r.get("file_summary_recall_count") for r in completed]
        ),
        "mean_stale_memory_rejection_count": num(
            [r.get("stale_memory_rejection_count") for r in completed]
        ),
        "failure_categories": dict(
            Counter(
                (r.get("failure_category") or "unknown")
                for r in completed
                if not r.get("passed")
            )
        ),
        "stop_reasons": dict(Counter(r.get("stop_reason") or "unknown" for r in completed)),
    }
    return out


def load_manifest(root: Path):
    path = root / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv):
    if not argv:
        print("usage: analyze_formal_experiments.py <run-root> [<run-root>...]")
        return 1
    for arg in argv:
        root = Path(arg)
        print(f"\n{'=' * 70}\nROOT: {root}\n{'=' * 70}")
        manifest = load_manifest(root)
        print("MANIFEST:", json.dumps({
            k: manifest.get(k)
            for k in (
                "experiment_run_id", "suite", "variant", "repeat",
                "git_commit", "provider", "model", "max_steps",
                "feature_flags", "created_at",
            )
        }, ensure_ascii=False, indent=2))
        rows = load_rows(root)
        summary = summarize_rows(rows, root.name)
        print("SUMMARY:", json.dumps(summary, ensure_ascii=False, indent=2))
        if manifest.get("suite") in {"context", "memory"}:
            # per-task breakdown
            by_task = defaultdict(list)
            for r in rows:
                by_task[r.get("task_id")].append(r)
            print("\nPER-TASK:")
            for tid in sorted(by_task):
                tr = summarize_rows(by_task[tid], tid)
                print(
                    f"  {tid}: pass={tr['passed']}/{tr['completed']} "
                    f"rate={tr['pass_rate']} tools={tr['mean_tool_steps']} "
                    f"in={tr['mean_input_tokens']} reads={tr['mean_read_calls']} "
                    f"rep_reads={tr['mean_repeated_read_calls']}"
                )
        if manifest.get("suite") == "recovery":
            print("\nRECOVERY ROWS:")
            for r in rows:
                print(
                    f"  {r.get('task_id')} rep={r.get('repeat_index')} "
                    f"status={r.get('status')} passed={r.get('passed')} "
                    f"verifier={r.get('verifier_passed')} "
                    f"fault={r.get('failure_category')} "
                    f"recovery_success={r.get('recovery_success')} "
                    f"unsafe_attempts={r.get('unsafe_operation_attempts')} "
                    f"unsafe_blocked={r.get('unsafe_operation_blocked')} "
                    f"tools={r.get('tool_steps')}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
