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
    result = {
        "attempts": (report or {}).get("attempts"),
        "tool_steps": (report or {}).get("tool_steps"),
        "read_calls": counts["read_file"],
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
    ):
        values = [row.get(key) for row in completed]
        summary[f"mean_{key}"] = _number(values)
        if key == "tool_steps":
            summary["median_tool_steps"] = _number(values, median)
    return summary
