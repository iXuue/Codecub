"""Assemble final Phase 3 Fast Validation numbers from the clean batches."""
import json
from pathlib import Path

base = Path(r"D:\代码备份\pico\pico-main\artifacts\phase3-fast-validation")
batch1 = base / "phase3-fast-validation-20260816-181505-8b590e"  # A/B isolated clean
batchC = base / "phase3-fast-validation-20260816-182017-9f8e92"  # C fresh seed + improved summaries

rows = []
for task in ("memory2_a_location_reuse", "memory2_b_workflow_recall"):
    for v in ("off", "on"):
        d = json.loads((batch1 / task / f"session-b-{v}.json").read_text(encoding="utf-8"))
        rows.append((d.get("task_id"), v, d))
for v in ("off", "on"):
    d = json.loads((batchC / "memory2_c_stale_safety" / f"session-b-{v}.json").read_text(encoding="utf-8"))
    rows.append((d.get("task_id"), v, d))

# corrected mem_hit for C on
ws = Path(json.loads((batchC / "memory2_c_stale_safety" / "session-b-on.json").read_text(encoding="utf-8"))["workspace"])
records = [
    json.loads(line)
    for line in (ws / ".codecub/memory/v2/evidence.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
c_mem_hit = any(r.get("path") == "codecub/runtime.py" and r.get("last_used_at") for r in records)

print(f"{'task':30s} {'var':3s} verifier steps first search repeats mem_hit inj_tokens")
for task, v, d in rows:
    print(
        f"{task:30s} {v:3s} {str(d.get('verifier_passed')):8s} "
        f"{d.get('tool_steps'):>5} {str(d.get('first_relevant_read_step')):>5} "
        f"{d.get('search_calls_before_relevant_read'):>6} {d.get('repeated_read_calls'):>7} "
        f"{str(d.get('memory_hit')):>7} {d.get('memory_injected_tokens')}"
    )
print()

off = [d for _, v, d in rows if v == "off"]
on = [d for _, v, d in rows if v == "on"]


def mean(key, rs):
    vals = [r.get(key) for r in rs if isinstance(r.get(key), (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


print("OFF verifier passes:", sum(bool(r.get("verifier_passed")) for r in off), "/ 3")
print("ON  verifier passes:", sum(bool(r.get("verifier_passed")) for r in on), "/ 3")
print("mean steps OFF/ON:", mean("tool_steps", off), "/", mean("tool_steps", on))
print("mean first_relevant_read OFF/ON:", mean("first_relevant_read_step", off), "/", mean("first_relevant_read_step", on))
print("mean search_before OFF/ON:", mean("search_calls_before_relevant_read", off), "/", mean("search_calls_before_relevant_read", on))
print("mean repeated_reads OFF/ON:", mean("repeated_read_calls", off), "/", mean("repeated_read_calls", on))
print("ON memory hits:", sum(bool(r.get("memory_hit")) for r in on), "/ 3  (C-on corrected:", c_mem_hit, ")")
print("ON stale_used_without_revalidation:", [r.get("memory_stale_used_without_revalidation") for r in on])
print("ON injected tokens:", [r.get("memory_injected_tokens") for r in on])
print("ON retrieval counts:", [r.get("memory_retrieval_count") for r in on])
print("ON guided rereads:", [r.get("memory_guided_reread_count") for r in on])
