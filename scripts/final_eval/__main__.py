"""Final Formal Evaluation orchestrator (evaluation-only).

Usage:
    python -m scripts.final_eval --phase preflight|run-main|run-stress|all

Phases keep raw artifacts; a product-infrastructure bug or leakage stops the
generation (spec §44, §69).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from .harness import (  # noqa: E402
    REPO_ROOT,
    build_agent,
    extract_frozen_tree,
    extract_run_metrics,
    generate_schedule,
    integrity_check,
    preflight_all,
    prune_workspace,
    run_agent,
    schedule_hash_of,
    task_manifest_hash,
    variant_workspace,
    write_frozen_manifest,
    write_holdout_manifest,
    write_row,
)
from .stats import build_statistics, write_machine_outputs  # noqa: E402
from .tasks import (  # noqa: E402
    FINAL_HOLDOUT_V1,
    MEMORY_SEEDED_TASK_IDS,
    REPEATS,
    STRESS_PLAN,
    V_FULL,
    VARIANT_FLAGS,
    task_by_id,
)

INTEGRITY_CHECKPOINTS = (3, 15, 30, 60)
SCHEDULE_SEED = 20260816


def output_root():
    return REPO_ROOT / "artifacts" / "final-eval"


def run_seed(task, seed_ws, flags, max_steps=16):
    """Session A predecessor run (memory ON). Returns seed memory source dir."""
    agent = build_agent(
        seed_ws, task, flags, max_steps=max_steps, requires_workspace_change=False
    )
    answer, retries = run_agent(agent, task.seed_prompt)
    v2 = agent.memory_v2
    memory_dir = seed_ws / ".codecub" / "memory" / "v2"
    return {
        "task_id": task.task_id,
        "answer": str(answer)[:400],
        "evidence_count": v2.evidence_store.size() if v2 else 0,
        "durable_count": v2.durable_store.size() if v2 else 0,
        "memory_dir": str(memory_dir),
        "retries": retries,
    }


def copy_seed_memory(src_v2, dst_workspace):
    dst = dst_workspace / ".codecub" / "memory" / "v2"
    dst.mkdir(parents=True, exist_ok=True)
    src = Path(src_v2)
    if src.exists():
        for path in src.iterdir():
            if path.is_file():
                shutil.copy2(path, dst / path.name)


def run_one(entry, root, output, seed_rows, all_rows, dry_run=False):
    task = task_by_id(entry["task_id"])
    variant = entry["variant"]
    repeat = entry["repeat"]
    flags = VARIANT_FLAGS[variant]
    ws = variant_workspace(root, task.task_id, variant, repeat)
    extract_frozen_tree(ws)
    prune_workspace(ws)
    # fresh fixture: no seed memory unless this task is memory-seeded
    seed_entry = next(
        (s for s in seed_rows if s.get("task_id") == task.task_id), None
    )
    if seed_entry and seed_entry.get("memory_dir"):
        copy_seed_memory(seed_entry["memory_dir"], ws)
    from .harness import apply_mutation

    apply_mutation(ws, task)
    agent = build_agent(ws, task, flags, max_steps=task.step_budget)
    started = time.monotonic()
    if dry_run:
        answer = "<dry-run>"
        retries = 0
    else:
        answer, retries = run_agent(agent, task.prompt)
    duration_ms = int((time.monotonic() - started) * 1000)
    row = extract_run_metrics(agent, task, variant, repeat, "main", ws)
    row["run_id"] = f"main-{entry['run_index']}-{uuid.uuid4().hex[:6]}"
    row["run_index"] = entry["run_index"]
    row["product_sha"] = "b0baa7a9e25de64c1d335ffd72cc9b498b64430e"
    row["task_hash"] = task_manifest_hash()[0]
    row["duration_ms"] = duration_ms
    row["provider_retry_count"] = retries
    row["answer"] = str(answer)[:400]
    write_row(output, "main", variant, row)
    all_rows.append(row)
    print(
        f"[run {entry['run_index']:>2}] {task.task_id} r{repeat} {variant}: "
        f"pass={row['passed']} verifier={row['verifier_passed']} steps={row['tool_steps']} "
        f"stop={row['stop_reason']}"
    )
    return row


def run_stress_one(task, fault, repeat, root, output, all_rows):
    ws = variant_workspace(root, task.task_id, "stress", repeat)
    extract_frozen_tree(ws)
    prune_workspace(ws)
    from .harness import apply_mutation

    apply_mutation(ws, task)
    agent = build_agent(ws, task, VARIANT_FLAGS[V_FULL], max_steps=task.step_budget)
    started = time.monotonic()
    answer, retries = run_agent(agent, task.prompt, fault=fault)
    duration_ms = int((time.monotonic() - started) * 1000)
    row = extract_run_metrics(agent, task, V_FULL, repeat, "stress", ws)
    row["run_id"] = f"stress-{task.task_id}-{fault}-{repeat}-{uuid.uuid4().hex[:6]}"
    row["product_sha"] = "b0baa7a9e25de64c1d335ffd72cc9b498b64430e"
    row["task_hash"] = task_manifest_hash()[0]
    row["duration_ms"] = duration_ms
    row["provider_retry_count"] = retries
    row["fault"] = fault
    row["answer"] = str(answer)[:400]
    write_row(output, "stress", V_FULL, row)
    all_rows.append(row)
    print(
        f"[stress] {task.task_id} r{repeat} fault={fault}: "
        f"pass={row['passed']} verifier={row['verifier_passed']} steps={row['tool_steps']}"
    )
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description="CodeCub 2.0 Final Formal Evaluation")
    parser.add_argument("--phase", choices=("preflight", "run", "stats", "all"), default="all")
    parser.add_argument("--dry-run", action="store_true", help="plumbing check (no API)")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N schedule entries (smoke test)")
    args = parser.parse_args(argv)

    output = output_root()
    output.mkdir(parents=True, exist_ok=True)
    runs_dir = output / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    if args.phase in ("preflight", "all"):
        print("== Preflight ==")
        preflight_all(output)
        print("preflight 10/10 PASS")

    # Frozen manifests + deterministic schedule (before any run).
    schedule = generate_schedule(SCHEDULE_SEED)
    schedule_hash = schedule_hash_of(schedule)
    write_frozen_manifest(output, schedule_hash)
    write_holdout_manifest(output)
    (output / "manifest" / "schedule.json").write_text(
        json.dumps(schedule, indent=2), encoding="utf-8"
    )
    print(f"== Schedule frozen: {len(schedule)} main runs (hash {schedule_hash[:12]}) ==")

    if args.phase not in ("run", "all"):
        return 0

    work_root = Path(__import__("tempfile").mkdtemp(prefix="codecub-final-"))
    seed_rows = []
    all_rows = []
    main_completed = 0

    # Precompute seed workspace roots per memory-seeded task (Session A).
    seeds_dir = output / "runs" / "seeds"
    seeds_dir.mkdir(parents=True, exist_ok=True)
    seeds_root = work_root / "seeds"
    for task in FINAL_HOLDOUT_V1:
        if task.task_id not in MEMORY_SEEDED_TASK_IDS:
            continue
        seed_ws = seeds_root / task.task_id
        extract_frozen_tree(seed_ws)
        prune_workspace(seed_ws)
        from .harness import apply_mutation

        apply_mutation(seed_ws, task)
        if args.dry_run:
            seed_rows.append(
                {
                    "task_id": task.task_id,
                    "answer": "<dry-run>",
                    "evidence_count": 0,
                    "durable_count": 0,
                    "memory_dir": "",
                }
            )
            continue
        print(f"== Seed (Session A): {task.task_id} ==")
        seed_rows.append(run_seed(task, seed_ws, VARIANT_FLAGS[V_FULL]))
        (seeds_dir / f"{task.task_id}.json").write_text(
            json.dumps(seed_rows[-1], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (seeds_dir / "seed_rows.json").write_text(
        json.dumps(seed_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.limit:
        schedule = schedule[: args.limit]
        print(f"== LIMIT {args.limit} schedule entries (smoke) ==")

    for entry in schedule:
        run_one(entry, work_root, output, seed_rows, all_rows, dry_run=args.dry_run)
        main_completed += 1
        if main_completed in INTEGRITY_CHECKPOINTS:
            issues = integrity_check(runs_dir, expected_rows=main_completed)
            print(f"== Integrity checkpoint @ {main_completed}: {'OK' if not issues else issues} ==")
            (output / "manifest" / f"integrity-{main_completed}.json").write_text(
                json.dumps({"completed": main_completed, "issues": issues}, indent=2),
                encoding="utf-8",
            )
            if issues:
                raise RuntimeError(f"integrity checkpoint failed at run {main_completed}: {issues}")

    # Main matrix audit (§47).
    main_issues = integrity_check(runs_dir, expected_rows=60)
    print(f"== Main matrix audit (60): {'OK' if not main_issues else main_issues} ==")
    if main_issues:
        raise RuntimeError(f"main matrix audit failed: {main_issues}")

    # Stress (§37-§38).
    if not args.dry_run:
        for task_id, fault in STRESS_PLAN.items():
            task = task_by_id(task_id)
            for repeat in range(1, REPEATS + 1):
                run_stress_one(task, fault, repeat, work_root, output, all_rows)

    # Persist full results.
    metrics_dir = output / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "final_runs.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stats = build_statistics(all_rows)
    write_machine_outputs(output, all_rows, stats)
    print("== Done. Machine outputs under", output, "==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
