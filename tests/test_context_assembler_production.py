"""Phase 7A production-path context evidence."""

from copy import deepcopy

from codecub import MiniAgent, SessionStore, WorkspaceContext
from codecub.models import ModelResponse, ToolCall


def test_native_inject_is_visible_in_the_next_provider_bound_context(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-context-spy"

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "read-1",
                            "read_file",
                            {"path": "README.md", "start": 1, "end": 1},
                        ),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
            self.requests.append(deepcopy(messages))
            return self.responses.pop(0)

    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    client = NativeClient()
    agent = MiniAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
    )
    provider_calls = {"count": 0}

    def inject_after_first_model_call():
        provider_calls["count"] += 1
        return ["Do not modify auth.py"] if provider_calls["count"] == 2 else []

    agent.injection_provider = inject_after_first_model_call

    assert agent.ask("inspect README", run_id="native-context-inject") == "Done."
    assert len(client.requests) == 2
    assert "Do not modify auth.py" not in str(client.requests[0])
    assert "Do not modify auth.py" in str(client.requests[1])
    assert agent.context_assembler.last_assembled_context.protocol == "native_tools"
