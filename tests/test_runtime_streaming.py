from pathlib import Path

from codecub.runtime import Pico, SessionStore
from codecub.workspace import WorkspaceContext


class StreamingFakeModelClient:
    supports_prompt_cache = False

    def __init__(self, chunks_by_call):
        self.chunks_by_call = [list(chunks) for chunks in chunks_by_call]
        self.prompts = []
        self.last_completion_metadata = {}

    def stream_complete(self, prompt, max_new_tokens, on_delta, **kwargs):
        del max_new_tokens, kwargs
        self.prompts.append(prompt)
        if not self.chunks_by_call:
            raise RuntimeError("fake model ran out of streaming chunks")
        chunks = self.chunks_by_call.pop(0)
        for chunk in chunks:
            on_delta(chunk)
        return "".join(chunks)


def make_agent(tmp_path, chunks_by_call):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(str(tmp_path))
    return Pico(
        model_client=StreamingFakeModelClient(chunks_by_call),
        workspace=workspace,
        session_store=SessionStore(Path(tmp_path) / ".codecub" / "sessions"),
        approval_policy="never",
        max_steps=3,
        max_new_tokens=512,
    )


def test_runtime_streams_only_final_answer_text(tmp_path):
    agent = make_agent(tmp_path, [["<final>Hel", "lo", " there</final>"]])
    events = []
    agent.event_handler = lambda name, payload, runtime, state: events.append((name, payload))

    answer = agent.ask("say hello")

    assert answer == "Hello there"
    assert [payload["text"] for name, payload in events if name == "assistant_delta"] == ["Hel", "lo", " there"]


def test_runtime_does_not_stream_tool_xml(tmp_path):
    agent = make_agent(
        tmp_path,
        [
            ['<tool name="read_file" path="README.md"></tool>'],
            ["<final>done</final>"],
        ],
    )
    events = []
    agent.event_handler = lambda name, payload, runtime, state: events.append((name, payload))

    answer = agent.ask("read")

    streamed = "".join(payload["text"] for name, payload in events if name == "assistant_delta")
    assert "<tool" not in streamed
    assert "</tool>" not in streamed
    assert answer == "done"
