from codecub.connections import RIGHTCODE_CLAUDE, RIGHTCODE_CODEX
from codecub.telemetry.contracts import build_usage_snapshot
from codecub.telemetry.parsers import parse_rightcode_claude_usage, parse_rightcode_codex_usage
from codecub.usage_store import UsageStore


def test_snapshot_never_merges_codex_responses_chat_and_claude():
    records = [
        parse_rightcode_codex_usage({"usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 80}}}, RIGHTCODE_CODEX, "gpt", "responses"),
        parse_rightcode_codex_usage({"usage": {"prompt_tokens": 20}}, RIGHTCODE_CODEX, "gpt", "chat_completions"),
        parse_rightcode_claude_usage({"usage": {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 70, "output_tokens": 5}}, RIGHTCODE_CLAUDE, "claude"),
    ]

    snapshot = build_usage_snapshot(records, "session", session_id="s1")

    assert len(snapshot["groups"]) == 3
    channels = {group["calculation_channels"]["context"] for group in snapshot["groups"]}
    assert channels == {"rightcode_codex_responses_usage", "rightcode_codex_chat_usage", "rightcode_claude_messages_usage"}


def test_claude_cache_ratio_uses_total_actual_input_including_cache_write():
    record = parse_rightcode_claude_usage(
        {"usage": {"input_tokens": 20, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 400}},
        RIGHTCODE_CLAUDE,
    )
    group = build_usage_snapshot([record], "request")["groups"][0]
    assert group["cache"]["read_ratio"] == 400 / 520


def test_usage_store_deduplicates_and_removes_private_response_fields(tmp_path):
    store = UsageStore(tmp_path / ".codecub" / "usage")
    record = {
        "usage_id": "u1",
        "session_id": "s1",
        "run_id": "r1",
        "connection_profile_id": "rightcode-codex",
        "context": {"actual_input_tokens": 10},
        "cache": {},
        "output": {},
        "cost": {},
        "raw_usage": {"secretish": "not-for-ui"},
        "provider_native_metrics": {"native": 1},
    }
    store.record(record)
    store.record(record)

    rows = store.load_records("s1")
    assert len(rows) == 1
    assert "raw_usage" not in rows[0]
    assert "provider_native_metrics" not in rows[0]
