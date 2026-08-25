import json
from unittest.mock import patch

from codecub import AnthropicCompatibleModelClient


def test_rightcode_claude_sends_stable_prefix_as_ephemeral_cache_block():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "text", "text": "<final>ok</final>"}], "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 2}}).encode()

    def open_request(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return Response()

    client = AnthropicCompatibleModelClient("claude", "https://www.right.codes/claude/v1", "sk-test", None, 30)
    with patch("urllib.request.urlopen", open_request):
        result = client.complete("STABLE\nDYNAMIC", 100, stable_prefix="STABLE\n")

    assert result == "<final>ok</final>"
    content = captured["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "STABLE\n", "cache_control": {"type": "ephemeral"}}
    assert content[1] == {"type": "text", "text": "DYNAMIC"}
    assert client.last_completion_metadata["prompt_cache_supported"] is True
