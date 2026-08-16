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
        self.requests.append(messages)
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
# 监视 compile_native 的替换行为
original_compile = agent.context_compiler.compile_native


def spy_compile(user_message, working_state=None, native_messages=None, pinned_extra=None):
    result = original_compile(user_message, working_state, native_messages, pinned_extra)
    out_messages = result[0]
    meta = result[1]
    print(f"[compile_native] in_len={len(native_messages)} out_len={len(out_messages)} compress={meta.get('should_compress')} same_ref={out_messages is native_messages}")
    return result


agent.context_compiler.compile_native = spy_compile
print("answer:", agent.ask("Update target.py"))
for i, messages in enumerate(client.requests):
    print(f"req{i} id={id(messages)} len={len(messages)}")
