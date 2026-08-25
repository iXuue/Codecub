from codecub.connections import resolve_connection_profile


def test_rightcode_codex_and_claude_are_distinct_connections():
    codex = resolve_connection_profile("https://www.right.codes/codex/v1", "openai-responses")
    claude = resolve_connection_profile("https://www.right.codes/claude/v1", "anthropic-messages")

    assert codex.id == "rightcode-codex"
    assert codex.protocol == "openai-responses"
    assert codex.model_vendor == "openai"
    assert claude.id == "rightcode-claude"
    assert claude.protocol == "anthropic-messages"
    assert claude.model_vendor == "anthropic"
    assert codex.credential_id == claude.credential_id == "rightcode"


def test_non_rightcode_endpoint_is_not_silently_classified_as_rightcode():
    profile = resolve_connection_profile("https://api.openai.com/v1", "openai-responses")
    assert profile.id == "openai-official"
    assert profile.connection_type == "direct"
    assert profile.api_operator == "openai"
