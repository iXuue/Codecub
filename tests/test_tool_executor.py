from codecub.agent.hooks import HookComposite, RuntimeHook
from codecub.tooling import GovernedToolExecutor, ToolExecutionContext


class Registry:
    def __init__(self, tools): self.tools = tools
    def resolve(self, name): return self.tools.get(name)


class Validation:
    def validate(self, name, args, tool):
        if tool.get("required") and "value" not in args:
            raise ValueError("missing value")
    def example(self, name): return "example" if name == "write" else ""
    def validate_result(self, tool, result): return (not result.startswith("bad"), "bad_result" if result.startswith("bad") else "")


class Approval:
    def __init__(self, allowed=True): self.allowed, self.calls = allowed, []
    def approve(self, name, args):
        self.calls.append((name, args))
        return self.allowed


class Replay:
    def __init__(self): self.claims, self.results, self.open = [], [], False
    def allow(self, name): return not self.open
    def status(self, name): return "open" if self.open else "closed"
    def claim(self, name, args, key, side_effect):
        if side_effect and key in self.claims:
            return {"claimed": False, "error_code": "side_effect_replay_blocked", "message": "error: replay"}
        if side_effect and key:
            self.claims.append(key)
        return {"claimed": True, "key": key}
    def complete(self, claim, success, metadata): self.completed = (claim, success, metadata)
    def record_result(self, name, success): self.results.append((name, success))


class Cancellation:
    def __init__(self, value=False): self.value = value
    def requested(self): return self.value


class Workspace:
    def __init__(self): self.changed = False
    def snapshot(self): return {"x": "old" if not self.changed else "new"}
    def diff(self, before, after): return (["x"] if before != after else [], ["modified:x"] if before != after else [])
    def fingerprint(self): return "fingerprint"


class Observation:
    def __init__(self): self.records = []
    def record(self, name, args, result, metadata): self.records.append((name, args, result, metadata))


def executor(tools, *, approval=True, cancelled=False):
    replay, workspace, observation = Replay(), Workspace(), Observation()
    context = ToolExecutionContext(Registry(tools), Validation(), Approval(approval), replay, Cancellation(cancelled), workspace, observation, object())
    return GovernedToolExecutor(context, HookComposite()), replay, workspace, observation


def test_standalone_tool_executor_governs_success_validation_and_replay():
    calls = []
    tool = {"risky": True, "side_effect": True, "required": True, "run": lambda args: calls.append(args) or "ok"}
    subject, replay, _workspace, observed = executor({"write": tool})
    assert subject.execute("write", {"value": "a"}, "op-1") == "ok"
    assert subject.execute("write", {"value": "a"}, "op-1") == "error: replay"
    assert calls == [{"value": "a"}]
    assert observed.records[-1][3]["tool_error_code"] == "side_effect_replay_blocked"
    assert "invalid arguments" in subject.execute("write", {})
    assert "unknown tool" in subject.execute("missing", {})
    assert replay.results == [("write", True)]


def test_standalone_tool_executor_applies_approval_cancellation_errors_and_hooks():
    events = []
    class Hooks(RuntimeHook):
        def before_tool(self, runtime, **payload): events.append("before")
        def after_tool(self, runtime, **payload): events.append("after")
    tool = {"risky": True, "side_effect": False, "run": lambda args: (_ for _ in ()).throw(RuntimeError("boom"))}
    subject, _replay, _workspace, observed = executor({"write": tool}, approval=False)
    subject.hooks = HookComposite([Hooks()])
    assert "approval denied" in subject.execute("write", {})
    assert events == ["before", "after"]
    subject, _replay, _workspace, observed = executor({"write": tool}, cancelled=True)
    assert "cancelled" in subject.execute("write", {})
    subject, _replay, _workspace, observed = executor({"write": tool})
    assert "failed: boom" in subject.execute("write", {})
    assert observed.records[-1][3]["tool_error_code"] == "tool_failed"


def test_standalone_tool_executor_blocks_open_circuit_and_normalizes_mutation():
    subject, replay, workspace, observed = executor(
        {"write": {"risky": True, "side_effect": True, "run": lambda args: setattr(workspace, "changed", True) or "ok"}}
    )
    replay.open = True
    assert "circuit is open" in subject.execute("write", {})
    assert observed.records[-1][3]["tool_status"] == "blocked"
    replay.open = False
    assert subject.execute("write", {}) == "ok"
    metadata = observed.records[-1][3]
    assert metadata["workspace_changed"] is True
    assert metadata["affected_paths"] == ["x"]


def test_standalone_tool_executor_normalizes_cancellation_during_tool():
    tool = {"risky": True, "side_effect": False, "run": lambda args: (_ for _ in ()).throw(RuntimeError("shell process cancelled"))}
    subject, _replay, _workspace, observed = executor({"shell": tool})
    assert "shell process cancelled" in subject.execute("shell", {})
    assert observed.records[-1][3]["tool_status"] == "error"
