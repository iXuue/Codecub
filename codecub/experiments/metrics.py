"""实验记录的可审计提取与聚合。未知数据保持为 None，绝不推测。"""

from collections import Counter
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
            seen.pop(str(path).replace("\\", "/"), None)
        if name != "read_file":
            continue
        path = str(args.get("path", "")).replace("\\", "/")
        start, end = int(args.get("start", 1)), int(args.get("end", 200))
        unique.add(path)
        prior = seen.get(path, [])
        if any(
            start <= previous_end and end >= previous_start
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
    result = {
        "attempts": (report or {}).get("attempts"),
        "tool_steps": (report or {}).get("tool_steps"),
        "read_calls": counts["read_file"],
        "unique_read_files": unique_reads,
        "repeated_read_calls": repeat_count,
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
    ):
        values = [row.get(key) for row in completed]
        summary[f"mean_{key}"] = _number(values)
        if key == "tool_steps":
            summary["median_tool_steps"] = _number(values, median)
    return summary
