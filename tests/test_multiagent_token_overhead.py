from types import SimpleNamespace

from scripts.eval_multiagent_token_overhead import (
    build_payload,
    mode_summary,
    percentage_change,
    usage_metrics,
)


def _usage(input_tokens=10, cached=2, output=4, total=14):
    return {
        "context": {"actual_input_tokens": input_tokens},
        "cache": {"read_tokens": cached},
        "output": {"output_tokens": output, "total_tokens": total},
    }


def test_usage_metrics_sums_all_provider_records_without_reconstruction():
    metrics = usage_metrics([_usage(), _usage(20, 3, 5, 25)])

    assert metrics["input_tokens"] == 30
    assert metrics["cached_input_tokens"] == 5
    assert metrics["output_tokens"] == 9
    assert metrics["total_tokens"] == 39
    assert metrics["usage_status"] == "complete"


def test_usage_metrics_marks_missing_provider_field_unavailable():
    row = _usage()
    row["cache"]["read_tokens"] = None

    metrics = usage_metrics([row])

    assert metrics["input_tokens"] == 10
    assert metrics["output_tokens"] == 4
    assert metrics["total_tokens"] == 14
    assert metrics["cached_input_tokens"] is None
    assert metrics["usage_status"] == "partial"


def test_mode_summary_uses_sum_and_not_average_of_task_percentages():
    rows = [
        {
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 2,
            "total_tokens": 12,
            "latency_seconds": 1.0,
            "task_passed": True,
            "usage_status": "complete",
        },
        {
            "input_tokens": 30,
            "cached_input_tokens": 0,
            "output_tokens": 6,
            "total_tokens": 36,
            "latency_seconds": 3.0,
            "task_passed": True,
            "usage_status": "complete",
        },
    ]
    variant = {"tasks": rows, "pairs": [{"pair_latency_seconds": 4.0}]}

    summary = mode_summary(variant)

    assert summary["total_tokens"] == 48
    assert summary["avg_latency_seconds"] == 2.0
    assert summary["token_per_passed_task"] == 24


def test_percentage_change_returns_unavailable_for_missing_or_zero_baseline():
    assert percentage_change(10, 0) is None
    assert percentage_change(None, 10) is None
    assert percentage_change(10, 20) == 50.0


def test_payload_keeps_full_workflow_invalid_for_existing_research_benchmark():
    result = SimpleNamespace(
        status="completed",
        answer="located",
        tool_steps=1,
        model_calls=1,
        elapsed_seconds=0.1,
        usage_records=(_usage(),),
    )
    serial = {
        "provider": "test",
        "model": "model",
        "max_steps": 2,
        "max_new_tokens": 128,
        "tasks": [],
        "pairs": [],
        "workspace_state_before": {"commit": "x", "dirty_paths": []},
        "workspace_state_after": {"commit": "x", "dirty_paths": []},
        "workspace_state_unchanged": True,
        "task_pairs": 5,
        "error_count": 0,
        "errors": [],
    }
    parallel = dict(serial)
    serial["tasks"] = [
        {
            "task_id": "pair-01-left",
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "output_tokens": 4,
            "total_tokens": 14,
            "latency_seconds": 1.0,
            "task_passed": True,
            "usage_status": "complete",
            "agents": {
                role: {"total_tokens": 0} for role in ("orchestrator", "research", "implement", "review")
            },
            "status": "completed",
        }
    ]
    parallel["tasks"] = [dict(serial["tasks"][0], mode="multi_agent")]
    serial["pairs"] = parallel["pairs"] = [{"pair_latency_seconds": 1.0, "workspace_state_unchanged": True}]
    payload = build_payload(serial, parallel)

    assert payload["validity"]["status"] == "MULTI_AGENT_TOKEN_EXPERIMENT_INVALID"
    assert any("Research only" in reason for reason in payload["validity"]["reasons"])
