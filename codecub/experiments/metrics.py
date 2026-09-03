"""实验记录的可审计提取与聚合。未知数据保持为 None，绝不推测。"""

from collections import Counter
import posixpath
import re
from statistics import mean, median


TOOL_FIELDS = {
    "read_file": "read_calls",
    "search": "search_calls",
    "patch_file": "patch_calls",
    "write_file": "write_calls",
    "run_shell": "shell_calls",
    "delegate": "delegate_calls",
    "dispatch": "dispatch_calls",
    "retrieve_code": "retrieval_calls",
}


def _number(values, fn=mean):
    values = [value for value in values if isinstance(value, (int, float))]
    return fn(values) if values else None


def percentage(numerator, denominator):
    return None if not denominator else numerator / denominator * 100


def reduction_percent(off, on):
    return (
        None if not isinstance(off, (int, float)) or not off else (off - on) / off * 100
    )


def repeated_reads(events):
    """同一路径、重叠区间、其间未被写入的 read_file 计为重复读取。"""

    def canonical_path(path):
        return posixpath.normpath(str(path).replace("\\", "/")).casefold()

    seen = {}
    repeats = 0
    unique = set()
    for event in events:
        if event.get("event") != "tool_executed":
            continue
        name, args = event.get("name"), event.get("args") or {}
        changed = (
            set(event.get("affected_paths") or [])
            if event.get("workspace_changed")
            else set()
        )
        for path in changed:
            seen.pop(canonical_path(path), None)
        if name != "read_file":
            continue
        path = canonical_path(args.get("path", ""))
        start, end = int(args.get("start", 1)), int(args.get("end", 200))
        unique.add(path)
        prior = seen.get(path, [])
        if any(
            (max(0, min(end, previous_end) - max(start, previous_start) + 1)
            / min(end - start + 1, previous_end - previous_start + 1))
            >= 0.8
            for previous_start, previous_end in prior
        ):
            repeats += 1
        seen.setdefault(path, []).append((start, end))
    return repeats, len(unique)


def extract_metrics(report, trace):
    counts = Counter()
    for event in trace:
        if event.get("event") == "tool_executed":
            counts[event.get("name")] += 1
    usage = (report or {}).get("usage_summary") or {}
    prompt_rows = [
        event.get("prompt_metadata") or {}
        for event in trace
        if event.get("event") == "prompt_built"
    ]
    repeat_count, unique_reads = repeated_reads(trace)
    seen_searches = set()
    repeated_searches = 0
    for event in trace:
        if event.get("event") != "tool_executed" or event.get("name") != "search":
            continue
        args = event.get("args") or {}
        signature = (
            posixpath.normpath(str(args.get("path", ".")).replace("\\", "/")).casefold(),
            re.sub(r"\s+", " ", str(args.get("pattern", "")).strip()).casefold(),
        )
        if signature in seen_searches:
            repeated_searches += 1
        seen_searches.add(signature)
    planning = (report or {}).get("planning") or {}
    watchdog = (report or {}).get("watchdog") or {}
    edit_watchdog = (report or {}).get("edit_decision_watchdog") or {}
    memory_v2 = (report or {}).get("memory_v2") or {}
    memory_activity = (report or {}).get("memory_v2_activity") or {}
    latest_prompt = prompt_rows[-1] if prompt_rows else {}
    compiler = latest_prompt.get("context_compiler") or {}
    hysteresis = compiler.get("hysteresis") or {}
    result = {
        "attempts": (report or {}).get("attempts"),
        "model_calls": (report or {}).get("attempts"),
        "tool_steps": (report or {}).get("tool_steps"),
        "runtime_mode": (report or {}).get("runtime_mode"),
        "effective_step_budget": (report or {}).get("effective_step_budget"),
        "emergency_cap": (report or {}).get("emergency_cap"),
        "stuck_suspected_count": watchdog.get("stuck_suspected_count", 0),
        "stuck_recovery_count": watchdog.get("recovery_turn_count", 0),
        "stuck_recovery_success_count": watchdog.get("recovery_success_count", 0),
        "stuck_confirmed_count": watchdog.get("stuck_confirmed_count", 0),
        "context_compile_count": compiler.get("context_compile_count"),
        "compression_count": compiler.get("compression_count", 0),
        "compression_failure_count": compiler.get("compression_failure_count", 0),
        "candidate_context_tokens": compiler.get("candidate_context_tokens"),
        "compiled_context_tokens": compiler.get("compiled_context_tokens"),
        "pinned_tokens": compiler.get("pinned_tokens"),
        "working_state_tokens": compiler.get("working_state_tokens"),
        "recent_verbatim_tokens": compiler.get("recent_verbatim_tokens"),
        "compressed_history_tokens": compiler.get("compressed_history_tokens"),
        "repo_map_tokens": compiler.get("repo_map_tokens"),
        "raw_history_tokens": compiler.get("raw_history_tokens"),
        "compiled_history_tokens": compiler.get("compiled_history_tokens"),
        "history_reduction_ratio": compiler.get("history_reduction_ratio"),
        "raw_model_visible_tokens": compiler.get("raw_model_visible_tokens"),
        "compiled_model_visible_tokens": compiler.get("compiled_model_visible_tokens"),
        "context_tokens_reclaimed": compiler.get("context_tokens_reclaimed"),
        "context_reduction_ratio": compiler.get("context_reduction_ratio"),
        "provider_actual_input_tokens": compiler.get("provider_actual_input_tokens"),
        "hysteresis_steps_since_last_compression": hysteresis.get(
            "steps_since_last_compression"
        ),
        "hysteresis_compression_skipped_no_gain": hysteresis.get(
            "compression_skipped_no_gain", 0
        ),
        "hysteresis_compression_thrashing_detected": hysteresis.get(
            "compression_thrashing_detected", False
        ),
        "hysteresis_high_watermark": hysteresis.get("high_watermark"),
        "hysteresis_target_watermark": hysteresis.get("target_watermark"),
        "edit_decision_count": planning.get("edit_decision_count", 0),
        "edit_decision_evidence_request_count": planning.get("evidence_request_count", 0),
        "edit_decision_invalid_count": planning.get("invalid_edit_decision_count", 0),
        "edit_decision_watchdog_total": edit_watchdog.get("total_decisions", 0),
        "edit_decision_watchdog_edits": edit_watchdog.get("edit_decisions", 0),
        "edit_decision_watchdog_evidence": edit_watchdog.get("evidence_decisions", 0),
        "edit_decision_watchdog_evidence_executed": edit_watchdog.get(
            "evidence_executed", 0
        ),
        "edit_decision_watchdog_evidence_rejected": edit_watchdog.get(
            "evidence_rejected_no_progress", 0
        ),
        "edit_decision_watchdog_no_progress_streak": edit_watchdog.get(
            "no_progress_streak", 0
        ),
        "fresh_fact_count": compiler.get("fresh_fact_count"),
        "stale_fact_count": compiler.get("stale_fact_count"),
        "read_calls": counts["read_file"],
        "files_read": unique_reads,
        "delegation_count": counts["delegate"] + counts["dispatch"],
        "parallel_subagent_count": 0,
        "retrieval_calls": counts["retrieve_code"],
        "semantic_retrieval_count": sum(
            bool(event.get("semantic_applied")) for event in trace
        ),
        "rerank_count": sum(bool(event.get("rerank_applied")) for event in trace),
        "retrieval_fallback_count": sum(
            event.get("retrieval_strategy") == "lexical_ast_rrf" for event in trace
        ),
        "retry_count": sum(event.get("event") == "model.retry" for event in trace),
        "fallback_count": sum(event.get("event") == "model.fallback" for event in trace),
        "circuit_break_count": sum(
            event.get("tool_error_code") == "circuit_open" for event in trace
        ),
        "unique_read_files": unique_reads,
        "repeated_read_calls": repeat_count,
        "repeated_search_calls": repeated_searches,
        "productive_exploration_steps": planning.get("productive_exploration_steps"),
        "redundant_exploration_steps": planning.get("redundant_exploration_steps"),
        "rejected_steps": planning.get("rejected_steps"),
        "first_action_step": planning.get("first_action_step"),
        "exploration_steps_before_first_action": planning.get("exploration_steps_before_first_action"),
        "exploration_warning_count": planning.get("exploration_warning_count", 0),
        "workspace_change_count": planning.get("workspace_change_count"),
        "first_workspace_change_step": planning.get("first_workspace_change_step"),
        "first_execution_step": planning.get("first_execution_step"),
        "first_verification_after_change_step": planning.get("first_verification_after_change_step"),
        "verification_steps": planning.get("verification_steps"),
        "verification_before_first_action": planning.get("verification_before_first_action"),
        "productive_verification_steps": planning.get("productive_verification_steps"),
        "redundant_verification_steps": planning.get("redundant_verification_steps"),
        "implementation_warning_count": planning.get("implementation_warning_count", 0),
        "avoidable_repeated_read_calls": planning.get("avoidable_repeated_read_calls", 0),
        "evidence_evicted_reread_calls": planning.get("evidence_evicted_reread_calls", 0),
        "evidence_ledger_entries": len(planning.get("evidence_ledger", []) or []),
        "evidence_eviction_count": planning.get("evidence_eviction_count", 0),
        "input_tokens": usage.get("actual_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cached_tokens": usage.get("cache_read_tokens"),
        "total_tokens": _sum_or_none(
            usage.get("actual_input_tokens"), usage.get("output_tokens")
        ),
        "prompt_chars": _number(
            [item.get("prompt_chars") for item in prompt_rows], sum
        ),
        "prompt_tokens": _number(
            [item.get("prompt_tokens") for item in prompt_rows], sum
        ),
        "max_prompt_size": _number(
            [item.get("prompt_chars") for item in prompt_rows], max
        ),
        "context_reduction_count": sum(
            len(item.get("budget_reductions") or []) for item in prompt_rows
        ),
        "checkpoint_count": sum(
            1 for event in trace if event.get("event") == "checkpoint_created"
        ),
        "memory_recall_count": sum(
            (item.get("relevant_memory") or {}).get("selected_count", 0)
            for item in prompt_rows
        ),
        "file_summary_recall_count": sum(
            (item.get("history") or {}).get("reused_file_summary_count", 0)
            for item in prompt_rows
        ),
        "stale_memory_rejection_count": len(
            ((report or {}).get("prompt_metadata") or {}).get("stale_paths") or []
        ),
        # Phase 3 — Memory 2.0 metrics（report["memory_v2"]）。
        "memory_v2_enabled": bool(memory_v2),
        "memory_candidate_count": memory_v2.get("candidate_count", 0),
        "memory_candidate_rejected_count": memory_v2.get("candidate_rejected_count", 0),
        "memory_candidate_promoted_count": memory_v2.get("candidate_promoted_count", 0),
        "memory_duplicate_count": memory_v2.get("duplicate_count", 0),
        "memory_superseded_count": memory_v2.get("superseded_count", 0),
        "memory_conflict_count": memory_v2.get("conflict_count", 0),
        "evidence_store_size": memory_v2.get("evidence_store_size"),
        "durable_store_size": memory_v2.get("durable_store_size"),
        "memory_retrieval_count": memory_v2.get("retrieval_count", 0),
        "memory_retrieved_evidence_count": memory_v2.get("retrieved_evidence_count", 0),
        "memory_retrieved_durable_count": memory_v2.get("retrieved_durable_count", 0),
        "memory_stale_evidence_count": memory_v2.get("stale_evidence_count", 0),
        "memory_revalidated_evidence_count": memory_v2.get("revalidated_evidence_count", 0),
        "memory_retrieval_tokens": memory_v2.get("retrieval_tokens", 0),
        "memory_injected_tokens": memory_v2.get("injected_tokens", 0),
        "memory_stale_used_without_revalidation": memory_v2.get(
            "stale_used_without_revalidation", 0
        ),
        "memory_guided_reread_count": memory_v2.get("memory_guided_reread_count", 0),
        "memory_v2_promotions": memory_activity.get("promotions") or [],
        "memory_v2_superseded": memory_activity.get("superseded") or [],
        "memory_v2_conflicts": memory_activity.get("conflicts") or [],
    }
    for tool, field in TOOL_FIELDS.items():
        result[field] = counts[tool]
    return result


def _sum_or_none(first, second):
    return (
        first + second if isinstance(first, int) and isinstance(second, int) else None
    )


def summarize(rows):
    completed = [row for row in rows if row.get("status") in {"pass", "fail"}]
    total, passed = len(completed), sum(bool(row.get("passed")) for row in completed)
    summary = {
        "total_runs": total,
        "passed_runs": passed,
        "pass_rate": percentage(passed, total),
        "verifier_pass_rate": percentage(
            sum(bool(row.get("verifier_passed")) for row in completed), total
        ),
        "within_budget_rate": percentage(
            sum(bool(row.get("within_budget")) for row in completed), total
        ),
        "failure_categories": dict(
            Counter(
                row.get("failure_category") or "unknown"
                for row in completed
                if not row.get("passed")
            )
        ),
    }
    for key in (
        "tool_steps",
        "read_calls",
        "search_calls",
        "input_tokens",
        "output_tokens",
        "duration_ms",
        "repeated_read_calls",
        "repeated_search_calls",
        "productive_exploration_steps",
        "redundant_exploration_steps",
        "rejected_steps",
        "exploration_warning_count",
        "first_action_step",
        "workspace_change_count",
        "first_workspace_change_step",
        "first_execution_step",
        "first_verification_after_change_step",
        "verification_steps",
        "verification_before_first_action",
        "productive_verification_steps",
        "redundant_verification_steps",
        "implementation_warning_count",
        "avoidable_repeated_read_calls",
        "evidence_evicted_reread_calls",
        "evidence_ledger_entries",
        "evidence_eviction_count",
        "stuck_suspected_count",
        "stuck_recovery_count",
        "stuck_recovery_success_count",
        "stuck_confirmed_count",
        "compression_count",
        "compression_failure_count",
        "candidate_context_tokens",
        "compiled_context_tokens",
        "pinned_tokens",
        "working_state_tokens",
        "recent_verbatim_tokens",
        "compressed_history_tokens",
        "repo_map_tokens",
        "raw_history_tokens",
        "compiled_history_tokens",
        "history_reduction_ratio",
        "raw_model_visible_tokens",
        "compiled_model_visible_tokens",
        "context_tokens_reclaimed",
        "context_reduction_ratio",
        "provider_actual_input_tokens",
        "hysteresis_steps_since_last_compression",
        "hysteresis_compression_skipped_no_gain",
        "edit_decision_count",
        "edit_decision_evidence_request_count",
        "edit_decision_invalid_count",
        "edit_decision_watchdog_total",
        "edit_decision_watchdog_edits",
        "edit_decision_watchdog_evidence",
        "edit_decision_watchdog_evidence_executed",
        "edit_decision_watchdog_evidence_rejected",
        "edit_decision_watchdog_no_progress_streak",
        "fresh_fact_count",
        "stale_fact_count",
        "memory_candidate_count",
        "memory_candidate_rejected_count",
        "memory_candidate_promoted_count",
        "memory_duplicate_count",
        "memory_superseded_count",
        "memory_conflict_count",
        "evidence_store_size",
        "durable_store_size",
        "memory_retrieval_count",
        "memory_retrieved_evidence_count",
        "memory_retrieved_durable_count",
        "memory_stale_evidence_count",
        "memory_revalidated_evidence_count",
        "memory_retrieval_tokens",
        "memory_injected_tokens",
        "memory_stale_used_without_revalidation",
        "memory_guided_reread_count",
    ):
        values = [row.get(key) for row in completed]
        summary[f"mean_{key}"] = _number(values)
        if key == "tool_steps":
            summary["median_tool_steps"] = _number(values, median)
    thrash = [
        bool(row.get("hysteresis_compression_thrashing_detected"))
        for row in completed
    ]
    if thrash:
        summary["thrash_detected_runs"] = sum(thrash)
    return summary
