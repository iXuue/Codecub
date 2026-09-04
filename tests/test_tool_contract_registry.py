import pytest

from codecub.tools import native_tool_definitions
from codecub.tooling import (
    ToolCapability,
    ToolEffect,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)
from codecub.agent.hooks import HookComposite


def test_registry_is_mapping_compatible_and_exposes_capability_metadata():
    registry = ToolRegistry()
    registry.register(
        "read_dynamic",
        {
            "description": "Read a dynamic resource.",
            "schema": {"query": "str"},
            "risky": False,
        },
    )
    registry.register(
        "write_dynamic",
        {
            "description": "Write a dynamic resource.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "risky": True,
            "effect": "write",
        },
    )

    assert set(registry) == {"read_dynamic", "write_dynamic"}
    assert registry.capability("read_dynamic") == ToolCapability(
        effect=ToolEffect.READ,
        concurrency_safe=True,
        idempotent=True,
        retryable=True,
    )
    assert registry.capability("write_dynamic").effect is ToolEffect.WRITE
    assert registry.resolve("read_dynamic") is registry["read_dynamic"]
    assert registry.has("read_dynamic") is True
    assert len(registry.get_definitions()) == 2

    definitions = native_tool_definitions(registry)
    write = next(item for item in definitions if item["function"]["name"] == "write_dynamic")
    assert write["function"]["parameters"]["required"] == ["value"]

    with pytest.raises(ValueError, match="already registered"):
        registry.register("read_dynamic", {"description": "collision"})

    filtered = registry.filtered({"read_dynamic"})
    assert set(filtered) == {"read_dynamic"}
    registry.unregister("write_dynamic")
    assert registry.resolve("write_dynamic") is None


class _Validation:
    def validate(self, name, args, tool):
        return None

    def example(self, name):
        return ""

    def validate_result(self, tool, result):
        return True, ""


class _Approval:
    read_only = False

    def approve(self, name, args):
        return True


class _Replay:
    limit = 5

    def allow(self, name):
        return True

    def status(self, name):
        return "closed"

    def claim(self, name, args, operation_key, side_effect):
        return {"claimed": True}

    def complete(self, claim, success, metadata):
        return None

    def record_result(self, name, success):
        return None


class _Cancellation:
    def requested(self):
        return False


class _Workspace:
    def snapshot(self):
        return {}

    def diff(self, before, after):
        return [], []

    def fingerprint(self):
        return "fingerprint"


class _Observation:
    def __init__(self):
        self.last_metadata = {}

    def record(self, name, args, result, metadata):
        self.last_metadata = dict(metadata)


def test_production_executor_emits_invocation_and_capability_metadata():
    registry = ToolRegistry(
        {
            "read_dynamic": {
                "description": "Read a dynamic resource.",
                "schema": {"query": "str"},
                "risky": False,
                "run": lambda args: "ok",
            }
        }
    )
    observation = _Observation()
    context = ToolExecutionContext(
        registry,
        _Validation(),
        _Approval(),
        _Replay(),
        _Cancellation(),
        _Workspace(),
        observation,
        object(),
        session_id="session-1",
        conversation_id="conversation-1",
        origin="SUBAGENT",
    )
    executor = ToolExecutor(context, HookComposite())

    assert executor.execute(
        "read_dynamic",
        {"query": "status"},
        call_id="call-1",
        turn_id="turn-1",
        iteration=2,
    ) == "ok"

    invocation = executor.last_invocation
    assert invocation.name == "read_dynamic"
    assert invocation.call_id == "call-1"
    assert invocation.session_id == "session-1"
    assert invocation.conversation_id == "conversation-1"
    assert invocation.turn_id == "turn-1"
    assert invocation.origin == "SUBAGENT"
    assert invocation.iteration == 2
    assert executor.last_execution.invocation == invocation
    assert executor.last_execution.duration_ms >= 0
    assert observation.last_metadata["tool_effect"] == "read"
    assert observation.last_metadata["tool_concurrency_safe"] is True
    assert observation.last_metadata["tool_call_id"] == "call-1"
