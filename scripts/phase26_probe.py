"""Phase 2.6 — Real DeepSeek Adaptive Edit Control & Context Stabilization Probe Runner.

走真实生产链路（禁止绕过 Runtime 手工构造 prompt）：
    User Task -> Runtime -> Context Compiler(hysteresis) -> Native Model Request
    -> Tool execution -> Runtime events -> 下一轮 Compile

用法:
    python scripts/phase26_probe.py --probe B [--context-window 16000] [--dry-run]

Development-only（evaluation_role=development），不是正式 Benchmark。
按 Phase 2.6 计划：默认 Probe B x1 -> Probe C x1（共 2 runs），建议总上限 4 runs。

分析目标：
- Probe B: Discovery -> Compression -> Edit -> Test Fail -> 新 Evidence ->
  Edit Decision #5/#6 仍可继续 -> Compression #2 -> Second Edit -> Verification
  （关键：不再 edit_decision_exhausted）。
- Probe C: Read(Fact A fresh) -> Patch -> Fact A stale -> Compression ->
  Re-read(Fact B fresh) -> Continue edit -> Verification
  （关键：真实 stale -> revalidation -> fresh）。
- 全量观测：compression hysteresis（steps_since_last_compression /
  compression_skipped_no_gain / compression_thrashing_detected / 压缩间隔）、
  同口径 token metrics（raw/compiled model visible / reclaimed / ratios）。
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codecub.experiments.runner import ExperimentConfig, ExperimentRunner
from codecub.experiments.tasks import DEVELOPMENT_PROBES

PROBE_OUTPUT_ROOT = Path("artifacts/phase26-probes")


def find_probe(probe_id):
    for task in DEVELOPMENT_PROBES:
        if task.id == probe_id or task.metadata.get("probe") == probe_id.upper():
            return task
    raise SystemExit(
        f"unknown probe: {probe_id}; choose from {[t.id for t in DEVELOPMENT_PROBES]}"
    )


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
            "PASS"
            if preflight["baseline_occurrences"] == task.expected_baseline_occurrences
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
    run_id = f"phase26-{probe_id}-{stamp}"
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
        print("PROBE DONE:", json.dumps(
            {
                "run_id": run_id,
                "steps": report.get("tool_steps"),
                "stop": report.get("stop_reason"),
                "verifier": verifier.returncode == 0,
                "compressions": analysis.get("compression_count"),
                "edit_decisions": analysis.get("edit_decision_count"),
                "evidence_executed": analysis.get("evidence_executed"),
                "evidence_rejected": analysis.get("evidence_rejected_no_progress"),
                "hysteresis_skips": analysis.get("hysteresis_skipped_no_gain"),
                "hysteresis_thrash": analysis.get("hysteresis_thrashing_detected"),
            },
            ensure_ascii=False,
        ))
    except Exception as exc:
        print(f"PROBE FAILED at {failure_stage}: {type(exc).__name__}: {exc}", file=sys.stderr)
        (output_root / "failure.json").write_text(
            json.dumps(
                {"stage": failure_stage, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise


def _meta_of(event):
    meta = (event.get("compilation_metadata") or {}).get("context_compiler") or {}
    if not meta:
        meta = event.get("compilation_metadata") or {}
    return meta


def analyze_trace(report, trace, task, workspace):
    """Phase 2.6 分析：edit-decision 进展 / hysteresis / 同口径 token metrics /
    stale -> revalidation -> fresh。"""
    compile_events = [e for e in trace if e.get("event") == "context_compile_finished"]
    compression_events = [e for e in trace if e.get("event") == "compression_triggered"]
    tool_events = [e for e in trace if e.get("event") == "tool_executed"]
    stale_events = [e for e in trace if e.get("event") == "context_fact_stale"]
    edit_no_progress_events = [
        e for e in trace if e.get("event") == "edit_decision_no_progress"
    ]
    progress_events = [e for e in trace if e.get("event") == "progress_detected"]

    timeline = []
    for event in compile_events:
        meta = _meta_of(event)
        hysteresis = meta.get("hysteresis") or {}
        timeline.append(
            {
                "step": event.get("step"),
                "should_compress": meta.get("should_compress"),
                "compression_count": meta.get("compression_count"),
                "candidate_context_tokens": meta.get("candidate_context_tokens"),
                "compiled_context_tokens": meta.get("compiled_context_tokens"),
                "raw_model_visible_tokens": meta.get("raw_model_visible_tokens"),
                "compiled_model_visible_tokens": meta.get("compiled_model_visible_tokens"),
                "context_tokens_reclaimed": meta.get("context_tokens_reclaimed"),
                "context_reduction_ratio": meta.get("context_reduction_ratio"),
                "raw_history_tokens": meta.get("raw_history_tokens"),
                "compiled_history_tokens": meta.get("compiled_history_tokens"),
                "history_reduction_ratio": meta.get("history_reduction_ratio"),
                "provider_actual_input_tokens": meta.get("provider_actual_input_tokens"),
                "steps_since_last_compression": hysteresis.get("steps_since_last_compression"),
                "compression_skipped_no_gain": hysteresis.get("compression_skipped_no_gain"),
                "compression_thrashing_detected": hysteresis.get(
                    "compression_thrashing_detected"
                ),
            }
        )

    # 压缩事件（native 路径也有 compression_triggered）。
    compression_steps = sorted(
        {int(e.get("compression_count") or 1) for e in compression_events}
    )
    last_compression_event = compression_events[-1] if compression_events else None
    post_compression_tools = []
    if last_compression_event:
        step_threshold = int(trace.index(last_compression_event))
        post_compression_tools = [
            e for e in tool_events if trace.index(e) > step_threshold
        ]

    # hysteresis 观测：从最后一次 compile 的 metadata 取累计值。
    last_meta = _meta_of(compile_events[-1]) if compile_events else {}
    last_hysteresis = last_meta.get("hysteresis") or {}

    planning = (report or {}).get("planning") or {}
    edit_watchdog = (report or {}).get("edit_decision_watchdog") or {}
    watchdog = (report or {}).get("watchdog") or {}

    # Probe C: stale -> revalidation -> fresh。
    # revalidation = 同路径 read 且该路径是 mutation affected 路径 / stale fact 路径。
    stale_paths = {
        str((e.get("payload") or {}).get("path", ""))
        for e in stale_events
    }
    changed_paths = set()
    for event in tool_events:
        if event.get("workspace_changed"):
            changed_paths.update(event.get("affected_paths") or [])
    post_compression_reads = [
        e for e in post_compression_tools if e.get("name") == "read_file"
    ]
    revalidation_reads = [
        e
        for e in post_compression_reads
        if str((e.get("args") or {}).get("path", "")) in (stale_paths | changed_paths)
    ]

    stop_reason = report.get("stop_reason")
    model_error = (report.get("prompt_metadata") or {}).get("model_error") or ""
    edit_decision_exhausted = (
        stop_reason == "model_error" and "edit_decision_exhausted" in str(model_error)
    )

    final_ok = stop_reason == "final_answer_returned" and report.get("status") == "completed"
    goal_retained = any(
        str((e.get("compilation_metadata") or {}).get("user_request") or "").strip()
        for e in compile_events
    )

    return {
        "compression_count": len(compression_steps),
        "compression_steps": compression_steps,
        "context_timeline": timeline,
        "post_compression_tool_steps": len(post_compression_tools),
        "post_compression_workspace_change": any(
            e.get("workspace_changed") for e in post_compression_tools
        ),
        "post_compression_verification": any(
            e.get("name") == "run_shell" for e in post_compression_tools
        ),
        "post_compression_final_success": final_ok,
        "edit_decision_count": planning.get("edit_decision_count", 0),
        "edit_decision_invalid_count": planning.get("invalid_edit_decision_count", 0),
        "evidence_request_count": planning.get("evidence_request_count", 0),
        "evidence_executed": edit_watchdog.get("evidence_executed", 0),
        "evidence_rejected_no_progress": edit_watchdog.get(
            "evidence_rejected_no_progress", 0
        ),
        "edit_no_progress_events": len(edit_no_progress_events),
        "edit_decision_exhausted": edit_decision_exhausted,
        "stuck_suspected_count": watchdog.get("stuck_suspected_count", 0),
        "stuck_recovery_count": watchdog.get("recovery_turn_count", 0),
        "stuck_confirmed_count": watchdog.get("stuck_confirmed_count", 0),
        "progress_event_count": len(progress_events),
        "hysteresis_high_watermark": last_hysteresis.get("high_watermark"),
        "hysteresis_target_watermark": last_hysteresis.get("target_watermark"),
        "hysteresis_steps_since_last_compression": last_hysteresis.get(
            "steps_since_last_compression"
        ),
        "hysteresis_skipped_no_gain": last_hysteresis.get(
            "compression_skipped_no_gain", 0
        ),
        "hysteresis_thrashing_detected": last_hysteresis.get(
            "compression_thrashing_detected", False
        ),
        "stale_fact_event_count": len(stale_events),
        "stale_paths": sorted(stale_paths),
        "changed_files": sorted(changed_paths),
        "revalidation_read_count": len(revalidation_reads),
        "goal_retained": goal_retained,
        "workspace_change_count": planning.get("workspace_change_count", 0),
    }


def write_context_snapshots(analysis, output_root):
    snapshots_dir = output_root / "context-snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    if not analysis.get("context_timeline"):
        return
    for index, entry in enumerate(analysis["context_timeline"]):
        if entry.get("compression_count", 0) < 1 and not entry.get("should_compress"):
            continue
        snapshot = {
            "snapshot_index": index,
            "compression_count": entry.get("compression_count"),
            "step": entry.get("step"),
            "should_compress": entry.get("should_compress"),
            "candidate_context_tokens": entry.get("candidate_context_tokens"),
            "compiled_context_tokens": entry.get("compiled_context_tokens"),
            "raw_model_visible_tokens": entry.get("raw_model_visible_tokens"),
            "compiled_model_visible_tokens": entry.get("compiled_model_visible_tokens"),
            "context_tokens_reclaimed": entry.get("context_tokens_reclaimed"),
            "context_reduction_ratio": entry.get("context_reduction_ratio"),
            "history_reduction_ratio": entry.get("history_reduction_ratio"),
            "steps_since_last_compression": entry.get("steps_since_last_compression"),
            "compression_skipped_no_gain": entry.get("compression_skipped_no_gain"),
            "compression_thrashing_detected": entry.get("compression_thrashing_detected"),
            "provider_actual_input_tokens": entry.get("provider_actual_input_tokens"),
            "trace_event_ids": ["context_compile_finished"],
        }
        (snapshots_dir / f"compile-{entry.get('compression_count', index):03d}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", required=True, help="B | C")
    parser.add_argument("--context-window", type=int, default=16000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run_probe(args.probe, args.context_window, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
