import sys
sys.path.insert(0, ".")
import tempfile
from pathlib import Path
from codecub import MiniAgent, SessionStore, WorkspaceContext
from codecub.models import ModelResponse, ToolCall


class NativeClient:
    supports_native_tools = True
    supports_prompt_cache = False
    model = "native-test"
    last_completion_metadata = {}

    def __init__(self):
        self.requests = []
        self.responses = [
            ModelResponse(tool_calls=(ToolCall("search", "search", {"pattern": "old", "path": "target.py"}),)),
            ModelResponse(tool_calls=(ToolCall("search", "search", {"pattern": "old", "path": "target.py"}),)),
            ModelResponse(tool_calls=(ToolCall("call-1", "patch_file", {"path": "target.py", "old_text": "old = 1", "new_text": "new = 1"}),)),
            ModelResponse(tool_calls=(ToolCall("verify-1", "run_shell", {"command": "python -c \"print('verified')\""}),)),
            ModelResponse(text="Done."),
        ]

    def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice="auto"):
        self.requests.append((messages, tools, tool_choice))
        return self.responses.pop(0)


tmp = Path(tempfile.mkdtemp())
(tmp / "target.py").write_text("old = 1\n", encoding="utf-8")
client = NativeClient()
agent = MiniAgent(
    model_client=client,
    workspace=WorkspaceContext.build(tmp),
    session_store=SessionStore(tmp / ".codecub" / "sessions"),
    approval_policy="auto",
    allowed_tools=("patch_file", "run_shell"),
    requires_workspace_change=True,
)
print("answer:", agent.ask("Update target.py"))
print("num requests:", len(client.requests))
for i, (messages, tools, choice) in enumerate(client.requests):
    roles = [m.get("role") for m in messages]
    contents = [str(m.get("content", ""))[:40] for m in messages]
    print(f"req{i}: roles={roles}")
    print(f"   contents={contents}")
