from codecub.models import iter_openai_text_deltas_from_sse


def test_iter_openai_text_deltas_from_responses_sse():
    body = "\n".join(
        [
            'data: {"type":"response.output_text.delta","delta":"Hel"}',
            'data: {"type":"response.output_text.delta","delta":"lo"}',
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":2}}}',
            "data: [DONE]",
        ]
    )

    assert list(iter_openai_text_deltas_from_sse(body.splitlines())) == ["Hel", "lo"]


def test_iter_openai_text_deltas_from_chat_completions_sse():
    body = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "data: [DONE]",
        ]
    )

    assert list(iter_openai_text_deltas_from_sse(body.splitlines())) == ["Hel", "lo"]
