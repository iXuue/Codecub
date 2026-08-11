import pytest

from codecub.connections import RIGHTCODE_CLAUDE, RIGHTCODE_CODEX
from codecub.telemetry import aggregate_usage_records
from codecub.telemetry.parsers import parse_rightcode_claude_usage, parse_rightcode_codex_usage


def test_codex_responses_usage_keeps_cached_and_uncached_input_separate():
    record = parse_rightcode_codex_usage(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "total_tokens": 1050,
                "input_tokens_details": {"cached_tokens": 800},
                "output_tokens_details": {"reasoning_tokens": 20},
            }
        },
        RIGHTCODE_CODEX,
        model="gpt-5",
        endpoint_kind="responses",
    )

    assert record["context"]["actual_input_tokens"] == 1000
    assert record["calculation_channels"]["context"] == "rightcode_codex_responses_usage"
    assert record["calculation_channels"]["cache"] == "rightcode_codex_responses_cache"
    assert record["cache"]["read_tokens"] == 800
    assert record["cache"]["uncached_input_tokens"] == 200
    assert record["output"]["reasoning_tokens"] == 20
    assert record["cost"]["operator_billed_cost"] is None


def test_codex_chat_fallback_never_claims_cache_support():
    record = parse_rightcode_codex_usage(
        {"usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 90}}},
        RIGHTCODE_CODEX,
        endpoint_kind="chat_completions",
    )

    assert record["cache"]["mode"] == "unavailable"
    assert record["calculation_channels"]["context"] == "rightcode_codex_chat_usage"
    assert record["calculation_channels"]["cache"] == "unsupported"
    assert record["cache"]["read_tokens"] is None
    assert record["cache"]["uncached_input_tokens"] == 100
    assert "rightcode_chat_cache_schema_unverified" in record["warnings"]


def test_claude_usage_counts_regular_write_and_read_context_components():
    record = parse_rightcode_claude_usage(
        {
            "usage": {
                "input_tokens": 20,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 400,
                "output_tokens": 30,
            }
        },
        RIGHTCODE_CLAUDE,
        model="claude-sonnet",
    )

    assert record["context"]["actual_input_tokens"] == 520
    assert record["calculation_channels"]["context"] == "rightcode_claude_messages_usage"
    assert record["calculation_channels"]["cache"] == "rightcode_claude_cache_usage"
    assert record["cache"]["uncached_input_tokens"] == 20
    assert record["cache"]["write_tokens"] == 100
    assert record["cache"]["read_tokens"] == 400
    assert record["output"]["total_tokens"] == 550


def test_missing_relay_usage_stays_unknown_instead_of_being_guessed():
    record = parse_rightcode_codex_usage({}, RIGHTCODE_CODEX)

    assert record["context"]["actual_input_tokens"] is None
    assert record["cache"]["read_tokens"] is None
    assert record["cost"]["cash_equivalent_cost"] is None
    assert "rightcode_usage_missing" in record["warnings"]


def test_aggregation_uses_weighted_cache_ratio_across_requests():
    records = [
        {
            "context": {"actual_input_tokens": 1000},
            "cache": {"read_tokens": 800, "write_tokens": 0, "uncached_input_tokens": 200},
            "output": {"output_tokens": 50, "reasoning_tokens": 20},
        },
        {
            "context": {"actual_input_tokens": 100},
            "cache": {"read_tokens": 0, "write_tokens": 10, "uncached_input_tokens": 100},
            "output": {"output_tokens": 10, "reasoning_tokens": 0},
        },
    ]

    summary = aggregate_usage_records(records)

    assert summary["actual_input_tokens"] == 1100
    assert summary["cache_read_tokens"] == 800
    assert summary["cache_write_tokens"] == 10
    assert summary["weighted_cache_read_ratio"] == pytest.approx(800 / 1100)
    assert summary["unknown_cost_request_count"] == 2
