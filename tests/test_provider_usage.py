from codecub.connections import ANTHROPIC_OFFICIAL, DEEPSEEK_OFFICIAL, OPENAI_OFFICIAL
from codecub.telemetry.parsers import parse_anthropic_usage, parse_openai_compatible_usage


def test_openai_official_has_its_own_responses_cache_channel():
    record = parse_openai_compatible_usage(
        {"usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 60}, "output_tokens": 10}},
        OPENAI_OFFICIAL,
        "gpt",
        "responses",
    )
    assert record["calculation_channels"]["context"] == "openai_responses_usage"
    assert record["calculation_channels"]["cache"] == "openai_responses_cache"
    assert record["cache"]["read_tokens"] == 60
    assert record["cache"]["uncached_input_tokens"] == 40


def test_deepseek_does_not_inherit_openai_or_rightcode_cache_semantics():
    record = parse_openai_compatible_usage(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 10}},
        DEEPSEEK_OFFICIAL,
        "deepseek-chat",
        "chat_completions",
    )
    assert record["calculation_channels"]["context"] == "deepseek_chat_completions_usage"
    assert record["calculation_channels"]["cache"] == "unverified"
    assert record["cache"]["read_tokens"] is None


def test_deepseek_responses_has_its_own_input_and_cache_channel():
    record = parse_openai_compatible_usage(
        {"usage": {"input_tokens": 1926, "input_tokens_details": {"cached_tokens": 768}, "output_tokens": 217}},
        DEEPSEEK_OFFICIAL,
        "deepseek-v4-flash",
        "responses",
    )
    assert record["calculation_channels"] == {
        "context": "deepseek_responses_usage",
        "cache": "deepseek_responses_cache_usage",
        "cost": "deepseek_official_pricing_unavailable",
    }
    assert record["context"]["actual_input_tokens"] == 1926
    assert record["cache"]["read_tokens"] == 768
    assert record["cache"]["uncached_input_tokens"] == 1158
    assert "deepseek_prompt_token_mismatch_or_missing" not in record["warnings"]


def test_deepseek_responses_keeps_input_when_cache_fields_are_missing():
    record = parse_openai_compatible_usage(
        {"usage": {"input_tokens": 100, "output_tokens": 10}},
        DEEPSEEK_OFFICIAL,
        "deepseek-v4-flash",
        "responses",
    )
    assert record["context"]["actual_input_tokens"] == 100
    assert record["cache"]["read_tokens"] is None
    assert record["cache"]["uncached_input_tokens"] is None
    assert "deepseek_responses_cache_fields_missing" in record["warnings"]


def test_anthropic_official_has_separate_messages_and_cache_channels():
    record = parse_anthropic_usage(
        {"usage": {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 70}},
        ANTHROPIC_OFFICIAL,
        "claude",
    )
    assert record["calculation_channels"]["context"] == "anthropic_messages_usage"
    assert record["calculation_channels"]["cache"] == "anthropic_cache_usage"
    assert record["context"]["actual_input_tokens"] == 100
