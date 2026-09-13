"""Measure token overhead for the existing five-pair research benchmark.

The benchmark is intentionally kept identical to ``eval_multiagent.py``.  It
compares the existing serial dispatch path with the existing parallel Research
dispatch path and records the Provider Usage already produced by CodeCub.
This script does not change prompts, role policies, scheduling, or benchmark
tasks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from codecub.cli import load_env_file
from scripts.eval_multiagent import TASKS, make_parent


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "experiments" / "multi_agent_token_overhead.md"
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "total_tokens",
)
ROLE_NAMES = ("orchestrator", "research", "implement", "review")


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _path_value(record, path):
    current = record
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _integer(current)


def _sum_provider_field(records, path):
    """Sum a field only when every observed Provider Usage row has it.

    Summing only the rows that happen to contain a field would undercount a
    run whose provider omitted that field.  Returning ``None`` makes the
    missing measurement visible to the report instead of inventing a value.
    """

    rows = [record for record in records if isinstance(record, dict)]
    if not rows:
        return None
    values = [_path_value(record, path) for record in rows]
    if any(value is None for value in values):
        return None
    return sum(values)


def usage_metrics(records):
    """Return the four requested token fields for one Agent.

    ``total_tokens`` is taken from the Provider Usage record.  It is not
    reconstructed from input/output fields when the provider omits it.
    """

    records = tuple(record for record in records if isinstance(record, dict))
    metrics = {
        "input_tokens": _sum_provider_field(
            records, ("context", "actual_input_tokens")
        ),
        "cached_input_tokens": _sum_provider_field(
            records, ("cache", "read_tokens")
        ),
        "output_tokens": _sum_provider_field(
            records, ("output", "output_tokens")
        ),
        "total_tokens": _sum_provider_field(
            records, ("output", "total_tokens")
        ),
    }
    known_count = sum(value is not None for value in metrics.values())
    if not records:
        status = "unavailable"
    elif known_count == len(metrics):
        status = "complete"
    elif known_count:
        status = "partial"
    else:
        status = "unavailable"
    metrics.update(
        {
            "usage_status": status,
            "usage_record_count": len(records),
        }
    )
    return metrics


def _not_invoked_agent(status):
    return {
        **{field: 0 for field in TOKEN_FIELDS},
        "usage_status": status,
        "usage_record_count": 0,
        "model_calls": 0,
    }


def _result_metrics(result):
    records = getattr(result, "usage_records", ()) if result is not None else ()
    metrics = usage_metrics(records)
    status = _effective_status(result)
    elapsed = (
        float(getattr(result, "elapsed_seconds", 0.0))
        if result is not None and status == "completed"
        else None
    )
    metrics.update(
        {
            "status": status,
            "model_calls": int(getattr(result, "model_calls", 0))
            if result is not None
            else 0,
            "tool_steps": int(getattr(result, "tool_steps", 0))
            if result is not None
            else 0,
            "latency_seconds": elapsed,
        }
    )
    return metrics


def _task_passed(result):
    if _effective_status(result) != "completed":
        return False
    return bool(str(getattr(result, "answer", "") or "").strip())


def _effective_status(result):
    if result is None:
        return "failed"
    status = str(getattr(result, "status", "failed") or "failed")
    answer = str(getattr(result, "answer", "") or "")
    # The current Orchestrator preserves the historical completed status when
    # Pico.ask returns a model-error answer.  The measurement must classify
    # that answer as a failed task without changing orchestration behavior.
    if status == "completed" and answer.startswith("Model error:"):
        return "failed"
    return status


def _error_kind(result):
    answer = str(getattr(result, "answer", "") or "") if result is not None else ""
    if "InvalidApiKey" in answer or "HTTP 401" in answer:
        return "invalid_api_key"
    if answer.startswith("Model error:"):
        return "model_error"
    return None


def _task_record(task_id, mode, task, result):
    agent = _result_metrics(result)
    agent["status"] = _effective_status(result)
    error_kind = _error_kind(result)
    if error_kind:
        agent["error_kind"] = error_kind
    if mode == "multi_agent":
        agents = {
            "orchestrator": _not_invoked_agent("not_an_llm_agent"),
            "research": agent,
            "implement": _not_invoked_agent("not_invoked"),
            "review": _not_invoked_agent("not_invoked"),
        }
    else:
        agents = {
            "orchestrator": _not_invoked_agent("not_an_llm_agent"),
            "research": _not_invoked_agent("not_applicable"),
            "implement": _not_invoked_agent("not_applicable"),
            "review": _not_invoked_agent("not_applicable"),
            "single_agent": agent,
        }
    return {
        "task_id": task_id,
        "mode": mode,
        "task": task,
        "latency_seconds": agent["latency_seconds"],
        **{field: agent[field] for field in TOKEN_FIELDS},
        "task_passed": _task_passed(result),
        "status": agent["status"],
        "agents": agents,
    }


def _git_state():
    def run(*args):
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout.strip()

    status = run("status", "--porcelain=v1", "--untracked-files=all")
    return {
        "commit": run("rev-parse", "HEAD"),
        "status": status,
        "dirty": bool(status),
        "dirty_paths": status.splitlines() if status else [],
    }


def _dispatch_pair(parent, left, right, parallel):
    if parallel:
        return parent.orchestrator.dispatch_many(
            [("research", left, 2), ("research", right, 2)]
        )
    return [
        parent.orchestrator.dispatch("research", left, 2),
        parent.orchestrator.dispatch("research", right, 2),
    ]


def _client_metadata(parent):
    client = getattr(parent.model_client, "client", parent.model_client)
    profile = getattr(client, "connection_profile", None)
    provider = str(getattr(client, "provider_name", "") or "").strip()
    if not provider:
        provider = str(getattr(profile, "id", "") or "").strip()
    model = str(getattr(client, "model", "") or "").strip()
    return provider or "unavailable", model or "unavailable"


def run_variant(mode, parallel):
    """Run one arm and retain every task, including failed tasks."""

    state_before = _git_state()
    parent = make_parent()
    began = time.monotonic()
    pairs = []
    task_records = []
    errors = []
    initialization_state_after = _git_state()

    for pair_index, (left, right) in enumerate(TASKS, start=1):
        pair_started = time.monotonic()
        pair_state_before = _git_state()
        try:
            results = _dispatch_pair(parent, left, right, parallel)
            if len(results) != 2:
                raise RuntimeError(
                    f"expected two results for pair {pair_index}, got {len(results)}"
                )
        except Exception as exc:  # retain both failed tasks in the report
            errors.append({"pair": pair_index, "error": str(exc)})
            results = [None, None]
        pair_elapsed = time.monotonic() - pair_started
        pair_state_after = _git_state()
        pair_unchanged = pair_state_before == pair_state_after

        pair_tasks = []
        for side, task, result in zip(("left", "right"), (left, right), results):
            row = _task_record(
                f"pair-{pair_index:02d}-{side}", mode, task, result
            )
            pair_tasks.append(row)
            task_records.append(row)
        pairs.append(
            {
                "pair_id": f"pair-{pair_index:02d}",
                "tasks": pair_tasks,
                "pair_latency_seconds": pair_elapsed,
                "workspace_state_unchanged": pair_unchanged,
                "state_before": pair_state_before,
                "state_after": pair_state_after,
            }
        )

    state_after = _git_state()
    counter = getattr(parent.model_client, "calls", None)
    provider, model = _client_metadata(parent)
    return {
        "mode": mode,
        "parallel": bool(parallel),
        "task_pairs": len(TASKS),
        "tasks": task_records,
        "pairs": pairs,
        "wall_clock_seconds": time.monotonic() - began,
        "model_calls": counter,
        "tool_calls": sum(row["agents"].get("research", {}).get("tool_steps", 0) for row in task_records),
        "error_count": len(errors),
        "errors": errors,
        "failed_task_count": sum(not row["task_passed"] for row in task_records),
        "workspace_state_before": state_before,
        "workspace_state_after_initialization": initialization_state_after,
        "runtime_initialization_changed_workspace": state_before != initialization_state_after,
        "workspace_state_after": state_after,
        "workspace_state_unchanged": state_before == state_after
        and state_before == initialization_state_after
        and all(pair["workspace_state_unchanged"] for pair in pairs),
        "provider": provider,
        "model": model,
        "max_steps": 2,
        "max_new_tokens": 128,
    }


def _sum_known(rows, field):
    values = [row.get(field) for row in rows]
    if not values or any(not isinstance(value, (int, float)) for value in values):
        return None
    return sum(values)


def _average_known(rows, field):
    values = [row.get(field) for row in rows]
    if not values or any(not isinstance(value, (int, float)) for value in values):
        return None
    return sum(values) / len(values)


def mode_summary(variant):
    rows = variant["tasks"]
    attempted_pair_average = _average_known(
        variant["pairs"], "pair_latency_seconds"
    )
    successful = bool(rows) and all(row["task_passed"] for row in rows)
    summary = {field: _sum_known(rows, field) for field in TOKEN_FIELDS}
    summary.update(
        {
            "avg_latency_seconds": _average_known(rows, "latency_seconds"),
            "attempted_pair_avg_latency_seconds": attempted_pair_average,
            "pair_avg_latency_seconds": attempted_pair_average if successful else None,
            "passed_tasks": sum(bool(row["task_passed"]) for row in rows),
            "task_count": len(rows),
            "usage_complete": all(
                row.get("usage_status") == "complete"
                for row in rows
            )
            and bool(rows),
        }
    )
    summary["token_per_passed_task"] = (
        summary["total_tokens"] / summary["passed_tasks"]
        if isinstance(summary["total_tokens"], (int, float))
        and summary["passed_tasks"]
        else None
    )
    return summary


def percentage_change(numerator, denominator):
    if not isinstance(numerator, (int, float)):
        return None
    if not isinstance(denominator, (int, float)) or denominator == 0:
        return None
    return numerator / denominator * 100.0


def _role_totals(variant):
    totals = {}
    for role in ROLE_NAMES:
        values = [
            row["agents"][role].get("total_tokens")
            for row in variant["tasks"]
        ]
        totals[role] = sum(values) if all(isinstance(value, int) for value in values) else None
    denominator = sum(value for value in totals.values() if isinstance(value, int))
    distribution = {
        role: (
            value / denominator * 100.0
            if isinstance(value, int) and denominator > 0
            else None
        )
        for role, value in totals.items()
    }
    return {"tokens": totals, "percentages": distribution}


def _load_historical_artifact():
    path = ROOT / "artifacts" / "multiagent_experiment.json"
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        return {
            "available": True,
            "path": str(path),
            "data": json.loads(path.read_text(encoding="utf-8")),
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "path": str(path), "error": str(exc)}


def _format(value, digits=2):
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _format_percent(value):
    return "unavailable" if value is None else f"{value:.2f}%"


def build_payload(serial, parallel):
    single = mode_summary(serial)
    multi = mode_summary(parallel)
    latency_reduction = percentage_change(
        single["pair_avg_latency_seconds"] - multi["pair_avg_latency_seconds"]
        if isinstance(single["pair_avg_latency_seconds"], (int, float))
        and isinstance(multi["pair_avg_latency_seconds"], (int, float))
        else None,
        single["pair_avg_latency_seconds"],
    ) if single["passed_tasks"] == single["task_count"] and multi["passed_tasks"] == multi["task_count"] else None
    token_overhead = percentage_change(
        multi["total_tokens"] - single["total_tokens"]
        if isinstance(multi["total_tokens"], (int, float))
        and isinstance(single["total_tokens"], (int, float))
        else None,
        single["total_tokens"],
    )
    same_model = serial["provider"] == parallel["provider"] and serial["model"] == parallel["model"]
    same_params = all(
        serial[key] == parallel[key]
        for key in ("max_steps", "max_new_tokens")
    )
    validity_reasons = []
    if not same_model:
        validity_reasons.append("serial and parallel provider/model metadata differ")
    if not same_params:
        validity_reasons.append("serial and parallel model parameters differ")
    if not serial["workspace_state_unchanged"] or not parallel["workspace_state_unchanged"]:
        validity_reasons.append("workspace state changed during at least one pair")
    if not single["usage_complete"] or not multi["usage_complete"]:
        validity_reasons.append("one or more Provider Usage records are incomplete")
    if single["passed_tasks"] < single["task_count"] or multi["passed_tasks"] < multi["task_count"]:
        validity_reasons.append("one or more tasks failed during the attempted run")
    # The existing benchmark has no Implement or Review dispatch and its
    # Parent is a local scheduler.  This is a measured Research-only arm, not
    # the full R -> I -> Review workflow requested by the specification.
    validity_reasons.extend(
        [
            "existing five-pair benchmark invokes Research only",
            "Single-Agent arm is not a full Research -> Implement -> Review task",
            "Parent/Orchestrator has no independent LLM call in this harness",
        ]
    )
    return {
        "experiment": "multi_agent_token_overhead",
        "benchmark": {
            "source": "scripts/eval_multiagent.py",
            "task_pairs": len(TASKS),
            "tasks": [
                {"pair_id": f"pair-{index:02d}", "left": left, "right": right}
                for index, (left, right) in enumerate(TASKS, start=1)
            ],
        },
        "single_agent": serial,
        "multi_agent": parallel,
        "summary": {
            "single_agent": single,
            "multi_agent": multi,
            "latency_reduction_percent": latency_reduction,
            "token_overhead_percent": token_overhead,
            "role_breakdown": _role_totals(parallel),
        },
        "fairness": {
            "same_tasks": True,
            "same_model": same_model,
            "same_parameters": same_params,
            "same_initial_repository_state": serial["workspace_state_before"] == parallel["workspace_state_before"],
            "same_tool_path_for_measured_arms": True,
            "same_initial_context_configuration": same_params and same_model,
            "all_five_pairs_recorded": len(serial["tasks"]) == 10 and len(parallel["tasks"]) == 10,
            "failures_retained": True,
            "full_requested_workflow_present": False,
        },
        "validity": {
            "status": "MULTI_AGENT_TOKEN_EXPERIMENT_INVALID",
            "reasons": validity_reasons,
        },
        "historical_artifact": _load_historical_artifact(),
    }


def _overall_table(payload):
    single = payload["summary"]["single_agent"]
    multi = payload["summary"]["multi_agent"]

    def change(field):
        left, right = single.get(field), multi.get(field)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return right - left
        return None

    lines = [
        "| Metric | Single-Agent | Multi-Agent | Change |",
        "| --- | ---: | ---: | ---: |",
    ]
    rows = (
        ("Total Token", "total_tokens"),
        ("Input Token", "input_tokens"),
        ("Cached Input Token", "cached_input_tokens"),
        ("Output Token", "output_tokens"),
        ("Avg Latency (s)", "pair_avg_latency_seconds"),
        ("Passed Tasks", "passed_tasks"),
        ("Token / Passed Task", "token_per_passed_task"),
    )
    for label, field in rows:
        lines.append(
            f"| {label} | {_format(single.get(field))} | "
            f"{_format(multi.get(field))} | {_format(change(field))} |"
        )
    return "\n".join(lines)


def _per_task_table(payload):
    single_rows = {row["task_id"]: row for row in payload["single_agent"]["tasks"]}
    multi_rows = {row["task_id"]: row for row in payload["multi_agent"]["tasks"]}
    lines = [
        "| Pair | Side | Task | Single status | Single total | Multi status | Multi total | Overhead | Passed S/M |",
        "| --- | --- | --- | --- | ---: | --- | ---: | ---: | --- |",
    ]
    for index, (left, right) in enumerate(TASKS, start=1):
        for side, task in (("left", left), ("right", right)):
            task_id = f"pair-{index:02d}-{side}"
            single = single_rows[task_id]
            multi = multi_rows[task_id]
            overhead = percentage_change(
                multi["total_tokens"] - single["total_tokens"]
                if isinstance(multi["total_tokens"], int)
                and isinstance(single["total_tokens"], int)
                else None,
                single["total_tokens"],
            )
            lines.append(
                f"| pair-{index:02d} | {side} | {task} | {single['status']} | "
                f"{_format(single['total_tokens'])} | {multi['status']} | "
                f"{_format(multi['total_tokens'])} | {_format_percent(overhead)} | "
                f"{str(single['task_passed'])[0].upper()}/"
                f"{str(multi['task_passed'])[0].upper()} |"
            )
    return "\n".join(lines)


def _pair_latency_table(payload):
    serial = payload["single_agent"]["pairs"]
    parallel = payload["multi_agent"]["pairs"]
    lines = [
        "| Pair | Single wall clock (s) | Multi wall clock (s) | Latency reduction | State unchanged |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for left, right in zip(serial, parallel):
        reduction = percentage_change(
            left["pair_latency_seconds"] - right["pair_latency_seconds"],
            left["pair_latency_seconds"],
        )
        lines.append(
            f"| {left['pair_id']} | {_format(left['pair_latency_seconds'])} | "
            f"{_format(right['pair_latency_seconds'])} | {_format_percent(reduction)} | "
            f"{left['workspace_state_unchanged'] and right['workspace_state_unchanged']} |"
        )
    return "\n".join(lines)


def build_report(payload):
    summary = payload["summary"]
    roles = summary["role_breakdown"]
    validity = payload["validity"]
    historical = payload["historical_artifact"]
    dirty_paths = payload["single_agent"]["workspace_state_before"]["dirty_paths"]
    multi = payload["multi_agent"]
    error_kinds = sorted(
        {
            row["agents"]["research"].get("error_kind")
            for row in multi["tasks"]
            if row["agents"]["research"].get("error_kind")
        }
    )
    cache_note = (
        "Provider 返回了 cached input token。"
        if summary["multi_agent"]["cached_input_tokens"] is not None
        else "Provider 未返回可验证的 cached input token，报告保留为 unavailable。"
    )
    reasons = "\n".join(f"- {reason}" for reason in validity["reasons"])
    role_lines = []
    for role in ROLE_NAMES:
        role_lines.append(
            f"| {role.capitalize()} | {_format(roles['tokens'][role])} | "
            f"{_format_percent(roles['percentages'][role])} |"
        )
    historical_note = "历史 artifact 不存在。"
    if historical.get("available"):
        old = historical.get("data", {})
        serial_ms = old.get("serial", {}).get("wall_clock_ms")
        parallel_ms = old.get("parallel", {}).get("wall_clock_ms")
        speedup = old.get("speedup")
        historical_note = (
            f"历史 artifact 记录 {serial_ms}ms → {parallel_ms}ms，"
            f"speedup={_format_percent(speedup * 100 if isinstance(speedup, (int, float)) else None)}；"
            "该文件只统计调用次数和墙钟时间，没有 Provider Token Usage，不能替代本轮数据。"
        )

    raw_records = {
        "single_agent": payload["single_agent"]["tasks"],
        "multi_agent": payload["multi_agent"]["tasks"],
    }

    return f"""# Multi-Agent Token Overhead Experiment

## Final status

`{validity['status']}`

本轮严格复用了 `scripts/eval_multiagent.py` 中的 5 组双任务。当前真实路径只创建 Research 子 Agent 并比较串行与并行 dispatch；Implement、Review 没有被调用，Parent/Orchestrator 也没有独立 LLM 请求。因此本报告给出实际可测的 Research-only Token/Latency 数据，但不能宣称完成完整的 Research → Implement → Review 对照。

## Overall Result

{_overall_table(payload)}

Token Overhead（按两臂所有任务 Token 总和计算）：**{_format_percent(summary['token_overhead_percent'])}**。

Latency Reduction（仅在两臂所有任务成功时计算）：**{_format_percent(summary['latency_reduction_percent'])}**。

本轮尝试的 pair wall-clock 仍记录在下表，但由于任务失败，不能作为有效性能结论。失败类型：`{', '.join(error_kinds) if error_kinds else 'unavailable'}`。

尝试性 pair wall-clock 平均值：serial `{_format(summary['single_agent']['attempted_pair_avg_latency_seconds'])}` 秒，parallel `{_format(summary['multi_agent']['attempted_pair_avg_latency_seconds'])}` 秒；该数值不进入有效 Latency Reduction。

{cache_note}

Provider/model metadata: serial `{payload['single_agent']['provider']}` / `{payload['single_agent']['model']}`; parallel `{payload['multi_agent']['provider']}` / `{payload['multi_agent']['model']}`. Observed model calls: serial `{payload['single_agent']['model_calls']}`, parallel `{payload['multi_agent']['model_calls']}`.

## Per-Task Result

以下列出全部 5 组、10 个任务；失败任务仍保留。

{_per_task_table(payload)}

## Pair Latency

{_pair_latency_table(payload)}

## Multi-Agent Token Breakdown

| Role | Total Token | Share |\n| --- | ---: | ---: |\n{chr(10).join(role_lines)}

当前 Multi-Agent measured arm 的 Parent 是本地调度器，Implement 和 Review 状态均为 `not_invoked`。这些角色的 0 表示本轮没有 LLM 请求，不是把缺失 Usage 当成 0。

## Fairness and data quality audit

| Check | Result |
| --- | --- |
| Exact existing tasks reused | {payload['fairness']['same_tasks']} |
| Same provider/model metadata | {payload['fairness']['same_model']} |
| Same model parameters | {payload['fairness']['same_parameters']} |
| Same initial repository state | {payload['fairness']['same_initial_repository_state']} |
| Same measured tool path | {payload['fairness']['same_tool_path_for_measured_arms']} |
| All 5 pairs and 10 tasks retained | {payload['fairness']['all_five_pairs_recorded']} |
| Workspace unchanged during pairs | {multi['workspace_state_unchanged'] and payload['single_agent']['workspace_state_unchanged']} |
| Runtime initialization left tracked status unchanged | {not payload['single_agent']['runtime_initialization_changed_workspace'] and not payload['multi_agent']['runtime_initialization_changed_workspace']} |
| Provider Usage complete for all four fields | {summary['single_agent']['usage_complete'] and summary['multi_agent']['usage_complete']} |
| Full requested R → I → Review workflow present | {payload['fairness']['full_requested_workflow_present']} |

The repository was already dirty at experiment start: `{len(dirty_paths)}` status entries, commit `{payload['single_agent']['workspace_state_before']['commit']}`. The experiment records that state and does not clean or overwrite it.

## Per-task JSON records

The following normalized records are the machine-readable measurements used for the tables above. `null` means the Provider did not return that field; non-LLM roles use zero together with an explicit `usage_status`.

```json
{json.dumps(raw_records, ensure_ascii=False, indent=2)}
```

## Where the extra tokens can come from

The current measured path provides evidence for one structural source: every Research dispatch constructs a fresh leaf `Pico` with its own role instructions and context assembly. The five pairs also issue independent repository lookups, so overlapping files can be read by separate children. These are the two directly observable sources in this harness.

The following requested sources are not measurable from this run because Implement, Review, and a Parent LLM call are absent: long Research handoffs, Review reloading unrelated context, Parent retaining child conversations, and tool-result duplication across those stages. Prompt-cache impact is likewise limited to the Provider Usage field reported above.

## Current Token waste Top 3

1. Fresh Research child context and system/role instructions for every task.
2. Overlapping repository retrieval performed independently by the two Research children.
3. The current benchmark does not exercise handoff boundaries, so any future R → I → Review run should first measure duplicated handoff and review context before optimizing it.

These are measurement-informed structural hypotheses; this run does not assign token quantities to them.

## Next experiment

First repair the benchmark mapping while preserving the same five task pairs: run a genuinely single-agent full task and the current production Multi-Agent Research → Implement → Review path with identical model settings and isolated initial workspaces. After that measurement is valid, a Context Handoff / Context Pruning ablation is worthwhile because fresh child context and repeated retrieval are visible cost candidates. It should remain a separate experiment after the baseline measurement.

## Historical latency reference

{historical_note}

## Invalidity reasons

{reasons}
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-path",
        default=str(REPORT_PATH),
        help="Markdown report path; defaults to experiments/multi_agent_token_overhead.md",
    )
    args = parser.parse_args(argv)

    load_env_file(".")
    serial = run_variant("single_agent", parallel=False)
    parallel = run_variant("multi_agent", parallel=True)
    payload = build_payload(serial, parallel)
    report_path = Path(args.report_path)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "status": payload["validity"]["status"],
                "token_overhead_percent": payload["summary"]["token_overhead_percent"],
                "latency_reduction_percent": payload["summary"]["latency_reduction_percent"],
            },
            ensure_ascii=False,
        )
    )
    return payload


if __name__ == "__main__":
    main()
