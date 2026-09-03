"""Phase 7B production gate, fallback, trace, and protocol-spy coverage."""

import json

from codecub import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from codecub.context_validator import (
    INVALID,
    REJECT,
    ContextValidationEvidence,
    ContextValidationResult,
    ContextValidator,
)
from codecub.models import ModelResponse, ToolCall


class RecordingValidator(ContextValidator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protocols = []

    def validate(self, context, *, protocol=None, policy=None, requirements=None):
        self.protocols.append(protocol or context.protocol)
        return super().validate(
            context,
            protocol=protocol,
            policy=policy,
            requirements=requirements,
        )


class HardInvalidValidator(ContextValidator):
    def validate(self, context, *, protocol=None, policy=None, requirements=None):
        self.validate_call_count += 1
        self.validation_count = self.validate_call_count
        evidence = ContextValidationEvidence(
            protocol=protocol or context.protocol,
            status=INVALID,
            action=REJECT,
            protected_constraints_present=False,
            failed_checks=("protected_constraints",),
            hard_failures=("PROTECTED_CONSTRAINT_MISSING",),
        )
        result = ContextValidationResult(
            INVALID, REJECT, evidence, "PROTECTED_CONSTRAINT_MISSING"
        )
        self.last_result = result
        self.last_evidence = evidence
        return result


class RetryThenRejectValidator(ContextValidator):
    def validate(self, context, *, protocol=None, policy=None, requirements=None):
        self.validate_call_count += 1
        self.validation_count = self.validate_call_count
        status = INVALID
        action = "RETRY_ASSEMBLY" if self.validate_call_count == 1 else REJECT
        evidence = ContextValidationEvidence(
            protocol=protocol or context.protocol,
            status=status,
            action=action,
            budget_ok=False,
            failed_checks=("budget",),
            hard_failures=("BUDGET_EXCEEDED",),
        )
        result = ContextValidationResult(status, action, evidence, "BUDGET_EXCEEDED")
        self.last_result = result
        self.last_evidence = evidence
        return result


def _agent(tmp_path, client, validator):
    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    return MiniAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        context_validator=validator,
    )


def _trace(agent, run_id):
    return [
        json.loads(line)
        for line in agent.run_store.trace_path(run_id).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_ordinary_production_path_validates_once_per_model_call_and_persists_evidence(tmp_path):
    client = FakeModelClient(["<final>done</final>"])
    validator = RecordingValidator()
    agent = _agent(tmp_path, client, validator)

    assert agent.ask("finish", run_id="validation-spy") == "done"

    trace = _trace(agent, "validation-spy")
    model_requests = [event for event in trace if event["event"] == "model_requested"]
    validations = [event for event in trace if event["event"] == "context_validated"]
    assert validator.validate_call_count == len(client.prompts) == 1
    assert len(validations) == len(model_requests) == 1
    assert validator.protocols == ["legacy_stream"]
    evidence = validations[0]["validation"]["evidence"]
    assert evidence["budget_ok"] is True
    assert evidence["final_tokens"] > 0
    assert validations[0]["validation"]["status"] in {"VALID", "VALID_WITH_FALLBACK"}


def test_hard_invalid_protected_context_is_blocked_before_model_invocation(tmp_path):
    client = FakeModelClient(["<final>must not run</final>"])
    validator = HardInvalidValidator()
    agent = _agent(tmp_path, client, validator)

    answer = agent.ask("finish", run_id="validation-hard-invalid")

    assert "PROTECTED_CONSTRAINT_MISSING" in answer
    assert len(client.prompts) == 0
    assert validator.validate_call_count == 1
    trace = _trace(agent, "validation-hard-invalid")
    assert sum(event["event"] == "context_validated" for event in trace) == 1
    assert sum(event["event"] == "model_requested" for event in trace) == 0


def test_retryable_validation_failure_has_one_bounded_reassembly_and_no_infinite_loop(tmp_path):
    client = FakeModelClient(["<final>must not run</final>"])
    validator = RetryThenRejectValidator(max_validation_attempts=1)
    agent = _agent(tmp_path, client, validator)

    answer = agent.ask("finish", run_id="validation-exhausted")

    assert "BUDGET_EXCEEDED" in answer
    assert len(client.prompts) == 0
    assert validator.validate_call_count == 2
    trace = _trace(agent, "validation-exhausted")
    assert sum(event["event"] == "context_validation_retry" for event in trace) == 1
    assert sum(event["event"] == "model_requested" for event in trace) == 0


def test_native_two_call_path_validates_each_provider_bound_assembly(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-validation-spy"

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall("read-1", "read_file", {"path": "README.md"}),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
            self.requests.append(messages)
            return self.responses.pop(0)

    client = NativeClient()
    validator = RecordingValidator()
    agent = _agent(tmp_path, client, validator)

    assert agent.ask("read README", run_id="native-validation-spy") == "Done."

    trace = _trace(agent, "native-validation-spy")
    model_requests = [event for event in trace if event["event"] == "model_requested"]
    validations = [event for event in trace if event["event"] == "context_validated"]
    assert len(client.requests) == len(validations) == len(model_requests) == 2
    assert validator.protocols == ["native_tools", "native_tools"]
    assert all(
        event["validation"]["evidence"]["native_continuity_ok"] is True
        for event in validations
    )
    assert all(
        trace.index(validation) < trace.index(request)
        for validation, request in zip(validations, model_requests)
    )


def test_native_continuity_failure_blocks_provider_call(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-validation-reject"

        def __init__(self):
            self.calls = 0

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
            self.calls += 1
            raise AssertionError("native provider must not receive invalid context")

    client = NativeClient()
    validator = ContextValidator()
    agent = _agent(tmp_path, client, validator)
    original = agent.context_collaborator.assemble_native

    def malformed_assembly(user_message, *, working_state, native_messages):
        messages, metadata = original(
            user_message,
            working_state=working_state,
            native_messages=native_messages,
        )
        messages.append(
            {"role": "tool", "tool_call_id": "unknown-call", "content": "orphan"}
        )
        return messages, metadata

    agent.context_collaborator.assemble_native = malformed_assembly

    answer = agent.ask("read README", run_id="native-validation-reject")

    assert "NATIVE_CONTINUITY_INVALID" in answer
    assert client.calls == 0
    trace = _trace(agent, "native-validation-reject")
    validation = next(event for event in trace if event["event"] == "context_validated")
    assert validation["validation"]["evidence"]["native_continuity_ok"] is False
    assert not any(event["event"] == "model_requested" for event in trace)


def test_stale_required_context_retries_once_then_blocks_provider(tmp_path):
    client = FakeModelClient(["<final>must not run</final>"])
    validator = ContextValidator(max_validation_attempts=1)
    agent = _agent(tmp_path, client, validator)
    validator.workspace_root = agent.root

    class StaleLedgerHook:
        def before_context(self, runtime, **payload):
            runtime.current_planning["evidence_ledger"] = [
                {
                    "path": "README.md",
                    "start": 1,
                    "end": 2,
                    "marker": "STALE-README",
                    "hint": "stale evidence",
                    "freshness": "stale-hash",
                }
            ]

    agent.hooks.hooks = (StaleLedgerHook(),)

    answer = agent.ask("use the README evidence", run_id="stale-validation-reject")

    assert "STALE_REQUIRED_CONTEXT" in answer
    assert len(client.prompts) == 0
    trace = _trace(agent, "stale-validation-reject")
    assert sum(event["event"] == "context_validated" for event in trace) == 2
    assert sum(event["event"] == "context_validation_retry" for event in trace) == 1
    assert not any(event["event"] == "model_requested" for event in trace)


def test_runtime_injection_is_present_in_protected_validation_evidence(tmp_path):
    client = FakeModelClient(["<final>safe</final>"])
    validator = RecordingValidator()
    agent = _agent(tmp_path, client, validator)
    injected = iter(["Do not modify README.md"])

    def next_injection():
        try:
            return [next(injected)]
        except StopIteration:
            return []

    agent.injection_provider = next_injection

    assert agent.ask("answer", run_id="injection-validation") == "safe"

    trace = _trace(agent, "injection-validation")
    validation = next(event for event in trace if event["event"] == "context_validated")
    evidence = validation["validation"]["evidence"]
    assert evidence["protected_constraints_present"] is True
    assert "Do not modify README.md" in client.prompts[0]


def test_streaming_and_optional_memory_or_retrieval_absence_remain_valid(tmp_path):
    client = FakeModelClient(["<final>streamed</final>"])
    validator = RecordingValidator()
    agent = _agent(tmp_path, client, validator)

    assert agent.ask("answer", run_id="stream-validation") == "streamed"

    validation = next(
        event for event in _trace(agent, "stream-validation") if event["event"] == "context_validated"
    )
    evidence = validation["validation"]["evidence"]
    assert evidence["protocol"] == "legacy_stream"
    assert evidence["memory_evidence_valid"] is None
    assert evidence["retrieval_evidence_valid"] is None
    assert evidence["valid"] is True
