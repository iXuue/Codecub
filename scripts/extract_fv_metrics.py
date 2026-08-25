"""Extract Phase 3 Fast Validation per-run metrics from run JSON files."""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if ROOT is None:
    raise SystemExit("usage: python extract_fv_metrics.py <run-root>")

KEYS = [
    "task_id", "variant", "verifier_passed", "verifier_exit_code",
    "tool_steps", "attempts", "duration_ms",
    "first_relevant_read_step", "search_calls_before_relevant_read",
    "repeated_read_calls", "unique_read_files",
    "memory_hit", "used_remembered_test_command",
    "memory_injected_tokens", "memory_retrieval_count",
    "memory_retrieved_evidence_count", "memory_retrieved_durable_count",
    "memory_stale_evidence_count", "memory_stale_used_without_revalidation",
    "memory_guided_reread_count", "evidence_store_size", "durable_store_size",
]

for task_dir in sorted(ROOT.glob("memory2_*")):
    for variant in ("off", "on"):
        path = task_dir / f"session-b-{variant}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        values = {k: data.get(k) for k in KEYS}
        print(f"--- {values.pop('task_id')} {values.pop('variant')}")
        for key, value in values.items():
            print(f"  {key}: {value}")
