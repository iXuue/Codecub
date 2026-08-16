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
            ModelResponse(tool_calls=(ToolCall("call-1", "patch_file", {"path": "target.py", "old_text": "old = 1", "new_text": "new = 1"}),)),
            ModelResponse(text="Done."),
        ]

    def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice="auto"):
        self.requests.append(([dict(m) for m in messages], tools, tool_choice))
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
for i, (messages, tools, choice) in enumerate(client.requests):
    print(f"req{i}: {len(messages)} messages")
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            print(f"   assistant tool_calls: {[c['id'] for c in m['tool_calls']]}")
        elif role == "tool":
            print(f"   tool: {m.get('tool_call_id')} -> {str(m.get('content'))[:30]}")
        else:
            print(f"   {role}: {str(m.get('content'))[:50]}")
