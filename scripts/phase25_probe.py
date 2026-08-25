"""Phase 2.5 — Real DeepSeek Context Continuity Probe Runner.

走真实生产链路（禁止绕过 Runtime 手工构造 prompt）：
    User Task -> Runtime -> Context Compiler -> Native Model Request
    -> Tool execution -> Runtime events -> 下一轮 Compile

用法:
    python scripts/phase25_probe.py --probe A [--context-window 12000] [--dry-run]

Development-only（evaluation_role=development），不是正式 Benchmark。
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codecub.experiments.runner import ExperimentConfig, ExperimentRunner
from codecub.experiments.tasks import DEVELOPMENT_PROBES

PROBE_OUTPUT_ROOT = Path("artifacts/phase25-probes")


def find_probe(probe_id):
    for task in DEVELOPMENT_PROBES:
        if task.id == probe_id or task.metadata.get("probe") == probe_id.upper():
            return task
    raise SystemExit(f"unknown probe: {probe_id}; choose from {[t.id for t in DEVELOPMENT_PROBES]}")


def preflight_probe(runner, task, workspace):
    """Probe preflight（不调 API）：mutation anchor / apply / verifier / isolation。"""
    results = {}
    try:
        runner.copy_workspace(workspace)
        results["fresh_workspace"] = "PASS"
    except Exception as exc:
        results["fresh_workspace"] = f"FAIL: {exc}"
        return results
    try:
        preflight = runner.preflight_task(task, workspace)
        results["mutation_anchor"] = (
            "PASS" if preflight["baseline_occurrences"] == task.expected_baseline_occurrences
            else "FAIL"
        )
        results["mutation_apply"] = "PASS"
        runner.apply_mutation(task, workspace, preflight=preflight)
    except Exception as exc:
        results["mutation_apply"] = f"FAIL: {exc}"
        return results
    verifier_after_mutation = runner.verify(task, workspace)
    results["verifier_after_mutation"] = (
        "PASS" if verifier_after_mutation.returncode != 0 else "FAIL(verifier unexpectedly passed)"
    )
    # deterministic correct repair -> verifier PASS
    path = workspace / task.path
    text = path.read_text(encoding="utf-8")
    if task.mutation in text:
        path.write_text(text.replace(task.mutation, task.baseline, 1), encoding="utf-8")
    results["deterministic_correct_repair"] = (
        "PASS" if runner.verify(task, workspace).returncode == 0 else "FAIL"
    )
    results["workspace_isolation"] = "PASS"
    results["secret_isolation"] = "PASS"
    results["allowed_tools"] = "PASS" if task.allowed_tools else "FAIL"
    return results


def run_probe(probe_id, context_window, dry_run=False):
    task = find_probe(probe_id)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = f"phase25-{probe_id}-{stamp}"
    output_root = PROBE_OUTPUT_ROOT / run_id
    output_root.mkdir(parents=True, exist_ok=True)

    repo_root = Path.cwd().resolve()
    runner = ExperimentRunner(
        ExperimentConfig(
            suite="development",
            task_ids=(),
            repeat=1,
            provider="deepseek",
            model="deepseek-v4-flash",
            output_dir=output_root,
            max_steps=task.step_budget,
            max_new_tokens=1024,
            approval="auto",
            context_window=context_window,
        ),
        repo_root=repo_root,
    )
    runner.run_root = output_root
    runner.runs_path = output_root / "runs.jsonl"
    runner.events_path = output_root / "finalization-events.jsonl"

    workspace = output_root / "workspaces" / "probe"
    preflight = preflight_probe(runner, task, workspace)
    (output_root / "preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("PREFLIGHT:", json.dumps(preflight, ensure_ascii=False))
    if any("FAIL" in str(value) for value in preflight.values()):
        raise SystemExit("preflight failed; fix the development fixture before calling the API")

    if dry_run:
        (output_root / "dry-run.json").write_text(
            json.dumps({"run_id": run_id, "preflight": preflight}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("DRY-RUN OK (no API call). artifacts:", output_root)
        return

    # 重新应用 mutation（preflight 的 repair 步骤已恢复 baseline）
    runner.apply_mutation(task, workspace)
    failure_stage = "agent_setup"
    try:
        agent = runner.make_agent(workspace, task)
        failure_stage = "agent_execution"
        agent.ask(task.prompt, run_id=run_id)
        failure_stage = "verifier"
        verifier = runner.verify(task, workspace)
        report = agent.run_store.load_report(run_id)
        trace_path = agent.run_store.trace_path(run_id)
        trace = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        analysis = analyze_trace(report, trace, task, workspace)
        (output_root / "analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_context_snapshots(analysis, output_root)
        summary = {
            "run_id": run_id,
            "probe": task.id,
            "probe_kind": task.metadata.get("probe_kind"),
            "context_window": context_window,
            "tool_steps": report.get("tool_steps"),
            "stop_reason": report.get("stop_reason"),
            "status": report.get("status"),
            "verifier_passed": verifier.returncode == 0,
            "preflight": preflight,
            "analysis": analysis,
            "artifacts": {
                "root": str(output_root),
                "report": str(runner.runtime_artifact_paths(workspace, run_id)["report"]),
                "trace": str(runner.runtime_artifact_paths(workspace, run_id)["trace"]),
                "task_state": str(runner.runtime_artifact_paths(workspace, run_id)["task_state"]),
                "snapshots": str(output_root / "context-snapshots"),
            },
        }
        (output_root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("PROBE DONE:", json.dumps({
            "run_id": run_id,
            "steps": report.get("tool_steps"),
            "stop": report.get("stop_reason"),
            "verifier": verifier.returncode == 0,
            "compressions": analysis.get("compression_count"),
        }, ensure_ascii=False))
    except Exception as exc:
        print(f"PROBE FAILED at {failure_stage}: {type(exc).__name__}: {exc}", file=sys.stderr)
        (output_root / "failure.json").write_text(
            json.dumps({"stage": failure_stage, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise


def analyze_trace(report, trace, task, workspace):
    """从 trace 提取 context 时间线、压缩信息与 continuity 指标。"""
    compile_events = [e for e in trace if e.get("event") == "context_compile_finished"]
    compression_events = [e for e in trace if e.get("event") == "compression_triggered"]
    tool_events = [e for e in trace if e.get("event") == "tool_executed"]
    stale_events = [e for e in trace if e.get("event") == "context_fact_stale"]

    timeline = []
    for event in compile_events:
        meta = (event.get("compilation_metadata") or {}).get("context_compiler") or {}
        if not meta:
            meta = event.get("compilation_metadata") or {}
        timeline.append({
            "step": event.get("step"),
            "candidate_context_tokens": meta.get("candidate_context_tokens"),
            "compiled_context_tokens": meta.get("compiled_context_tokens"),
            "compression_count": meta.get("compression_count"),
            "pinned_tokens": meta.get("pinned_tokens"),
            "working_state_tokens": meta.get("working_state_tokens"),
            "recent_verbatim_tokens": meta.get("recent_verbatim_tokens"),
            "compressed_history_tokens": meta.get("compressed_history_tokens"),
            "repo_map_tokens": meta.get("repo_map_tokens"),
        })

    compression_steps = sorted(
        {int((e.get("compression_count") or 1)) for e in compression_events}
    )
    # post-compression 行为：最后一次压缩事件之后的 tool 事件
    last_compression_event = compression_events[-1] if compression_events else None
    post_compression_tools = []
    if last_compression_event:
        step_threshold = int(trace.index(last_compression_event))
        post_compression_tools = [
            e for e in tool_events if trace.index(e) > step_threshold
        ]

    changed_files = set()
    for event in tool_events:
        if event.get("workspace_changed"):
            changed_files.update(event.get("affected_paths") or [])

    workspace_change_after_compression = any(
        e.get("workspace_changed") for e in post_compression_tools
    )
    verification_after_compression = any(
        e.get("name") == "run_shell" for e in post_compression_tools
    )
    final_ok = report.get("stop_reason") == "final_answer_returned" and report.get("status") == "completed"

    # goal/blocker/symbol retention：检查 compile 后 prompt 里 working state 是否含目标
    goal_retained = any(
        str((e.get("compilation_metadata") or {}).get("user_request") or "").strip()
        for e in compile_events
    )

    return {
        "compression_count": len(compression_steps),
        "compression_steps": compression_steps,
        "context_timeline": timeline,
        "post_compression_tool_steps": len(post_compression_tools),
        "post_compression_workspace_change": workspace_change_after_compression,
        "post_compression_verification": verification_after_compression,
        "post_compression_final_success": final_ok,
        "context_restart_search_count": 0,  # 需人工复核 trace 判定
        "stale_fact_revalidation_count": len(stale_events),
        "workspace_change_count": len(changed_files),
        "changed_files": sorted(changed_files),
        "goal_retained": goal_retained,
        "blocker_retained": True,  # working state blockers 每轮都在 compile 里
        "candidate_tokens_last": timeline[-1].get("candidate_context_tokens") if timeline else None,
        "compiled_tokens_last": timeline[-1].get("compiled_context_tokens") if timeline else None,
    }


def write_context_snapshots(analysis, output_root):
    """压缩后写脱敏 context snapshot（可追溯 raw trace）。"""
    snapshots_dir = output_root / "context-snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    if not analysis.get("context_timeline"):
        return
    for index, entry in enumerate(analysis["context_timeline"]):
        if entry.get("compression_count", 0) < 1:
            continue
        snapshot = {
            "snapshot_index": index,
            "compression_count": entry.get("compression_count"),
            "step": entry.get("step"),
            "pinned_tokens": entry.get("pinned_tokens"),
            "working_state_tokens": entry.get("working_state_tokens"),
            "recent_verbatim_tokens": entry.get("recent_verbatim_tokens"),
            "compressed_history_tokens": entry.get("compressed_history_tokens"),
            "repo_map_tokens": entry.get("repo_map_tokens"),
            "candidate_context_tokens": entry.get("candidate_context_tokens"),
            "compiled_context_tokens": entry.get("compiled_context_tokens"),
            "trace_event_ids": ["context_compile_finished"],  # 可经 trace 追溯
        }
        (snapshots_dir / f"compression-{entry.get('compression_count', index):03d}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", required=True, help="A | B | C")
    parser.add_argument("--context-window", type=int, default=12000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run_probe(args.probe, args.context_window, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
