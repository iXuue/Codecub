"""Phase 6 boundaries are intentionally surface-free."""

import ast
import inspect
import json
from pathlib import Path
import threading

import pytest

from codecub.agent import LegacyLoopStateAdapter, LegacyModelInvoker
from codecub.agent.hooks import HookComposite, RuntimeHook
from codecub.agent.runner import LoopOutcome, TurnRunner
from codecub.models import FakeModelClient, ModelResponse, ToolCall
from codecub.runtime import Pico
from codecub.sessions import SessionManager, SessionStore
from codecub.workspace import WorkspaceContext
from codecub.context_compiler import WorkingState
from codecub.watchdog import ProgressWatchdog


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "relative, forbidden",
    [
        ("codecub/agent/loop.py", {"codecub.cli", "codecub.app_runner", "codecub.run_queue"}),
        ("codecub/agent/runner.py", {"codecub.cli", "codecub.app_runner", "codecub.run_queue"}),
        ("codecub/tooling/executor.py", {"codecub.cli", "codecub.app_runner"}),
        ("codecub/sessions/manager.py", {"codecub.cli", "codecub.app_runner"}),
    ],
)
def test_phase6_boundaries_do_not_import_surfaces(relative, forbidden):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    imports = {
        node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)
    } | {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imports & forbidden


def test_session_manager_persists_and_normalizes_checkpoint_shape(tmp_path):
    manager = SessionManager(SessionStore(tmp_path), tmp_path, lambda: {"facts": []})
    session = manager.create({"id": "s1", "checkpoints": None}, now="now")
    manager.save(session)
    loaded = manager.load("s1")
    assert loaded["checkpoints"] == {"current_id": "", "items": {}}
    assert loaded["memory"] == {"facts": []}


def test_hooks_are_ordered_and_noncritical_failures_are_isolated():
    events = []

    class First(RuntimeHook):
        def before_turn(self, runtime, **payload):
            events.append(("first", payload["run_id"]))

    class Broken(RuntimeHook):
        def before_turn(self, runtime, **payload):
            raise RuntimeError("telemetry unavailable")

    class Last(RuntimeHook):
        def before_turn(self, runtime, **payload):
            events.append(("last", payload["run_id"]))

    HookComposite([First(), Broken(), Last()]).before_turn(object(), run_id="r")
    assert events == [("first", "r"), ("last", "r")]


def test_critical_hook_failure_propagates():
    class Critical(RuntimeHook):
        critical = True

        def on_error(self, runtime, **payload):
            raise RuntimeError("must stop")

    with pytest.raises(RuntimeError, match="must stop"):
        HookComposite([Critical()]).on_error(object(), error=ValueError())


def test_runtime_hooks_wrap_real_turn_context_and_tool_execution(tmp_path):
    events = []

    class Recorder(RuntimeHook):
        def before_turn(self, runtime, **payload): events.append("before_turn")
        def before_context(self, runtime, **payload): events.append("before_context")
        def after_context(self, runtime, **payload): events.append("after_context")
        def before_model(self, runtime, **payload): events.append("before_model")
        def after_model(self, runtime, **payload): events.append("after_model")
        def before_tool(self, runtime, **payload): events.append("before_tool")
        def after_tool(self, runtime, **payload): events.append("after_tool")
        def after_turn(self, runtime, **payload): events.append("after_turn")

    agent = Pico(
        model_client=FakeModelClient([
            '<tool name="read_file" path="README.md" start="1" end="1"></tool>',
            "<final>done</final>",
        ]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="never",
        runtime_hooks=[Recorder()],
    )
    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    assert agent.ask("read") == "done"
    assert events == [
        "before_turn", "before_context", "after_context", "before_model", "after_model",
        "before_tool", "after_tool", "before_context", "after_context", "before_model",
        "after_model", "after_turn",
    ]


def test_turn_runner_emits_cancel_and_error_hooks_once():
    events = []

    class Hooks:
        def before_turn(self, *args, **kwargs): events.append("before")
        def after_turn(self, *args, **kwargs): events.append("after")
        def on_cancel(self, *args, **kwargs): events.append("cancel")
        def on_error(self, *args, **kwargs): events.append("error")

    class Runtime:
        current_task_state = object()
        def initialize(self, *_args): return object()
        def cancellation_requested(self, state): return True

    class Loop:
        def run(self, message, run_id="", preparation=None): return "done"

    assert TurnRunner(Runtime(), Loop(), Hooks()).run("x", "r") == "done"
    assert events == ["before", "cancel", "after"]

    class BrokenLoop:
        def run(self, message, run_id="", preparation=None): raise ValueError("model failed")

    with pytest.raises(ValueError, match="model failed"):
        TurnRunner(Runtime(), BrokenLoop(), Hooks()).run("x", "r")
    assert events[-2:] == ["before", "error"]


def test_turn_runner_finalizes_loop_outcomes_without_runtime():
    events = []

    class Hooks:
        def before_turn(self, *_args, **_kwargs): events.append("before")
        def after_turn(self, *_args, **_kwargs): events.append("after")
        def on_cancel(self, *_args, **_kwargs): events.append("cancel")

    class State:
        run_id = "run-outcome"
        def finish_success(self, answer): events.append(("state", answer))

    class Lifecycle:
        hook_subject = object()
        def initialize(self, *_args): return object()
        def emit_status(self, *_args): events.append("status")
        def record_assistant(self, answer): events.append(("record", answer))
        def enrich_memory(self, outcome): events.append(("memory", outcome.kind))
        def create_checkpoint(self, *_args): events.append("checkpoint")
        def write_task_state(self, *_args): events.append("state_saved")
        def emit_checkpoint_created(self, *_args): events.append("checkpoint_event")
        def emit_run_finished(self, *_args): events.append("finished_event")
        def write_final_report(self, *_args): events.append("report")
        def cancellation_requested(self): return False

    class Loop:
        def run(self, message, run_id="", preparation=None):
            return LoopOutcome("done", "success", State(), message, 1.0, "now")

    assert TurnRunner(Lifecycle(), Loop(), Hooks()).run("x", "r") == "done"
    assert events == [
        "before", "status", ("record", "done"), ("state", "done"),
        ("memory", "success"), "checkpoint", "state_saved", "checkpoint_event",
        "finished_event", "status", "report", "after",
    ]


def test_turn_runner_declares_all_legacy_terminal_outcome_kinds():
    source = (ROOT / "codecub" / "agent" / "runner.py").read_text(encoding="utf-8")
    for kind in (
        "success", "limited", "model_error", "cancelled",
        "finalization_failed", "stuck", "emergency_cap",
    ):
        assert f'kind == "{kind}"' in source


def test_production_ask_enters_turn_runner_loop_and_finalizes_once(tmp_path):
    agent = Pico(
        model_client=FakeModelClient(["<final>done</final>"]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="never",
    )
    calls = []
    original_initialize = agent.turn_runner.lifecycle.initialize
    original_loop_run = agent.agent_loop.run
    original_finalize = agent.turn_runner.finalize

    def initialize(*args, **kwargs):
        calls.append("initialize")
        return original_initialize(*args, **kwargs)

    def run_loop(*args, **kwargs):
        calls.append("loop")
        return original_loop_run(*args, **kwargs)

    def finalize(outcome):
        calls.append(("finalize", outcome.kind))
        return original_finalize(outcome)

    agent.turn_runner.lifecycle.initialize = initialize
    agent.agent_loop.run = run_loop
    agent.turn_runner.finalize = finalize

    assert agent.ask("finish", run_id="run-production-spy") == "done"
    trace = (tmp_path / ".codecub" / "runs" / "run-production-spy" / "trace.jsonl")
    event_names = [json.loads(line)["event"] for line in trace.read_text(encoding="utf-8").splitlines()]

    assert calls == ["initialize", "loop", ("finalize", "success")]
    assert event_names.count("run_finished") == 1


def test_loop_state_adapter_has_explicit_state_and_observer_dependencies(tmp_path):
    class Observer:
        def __init__(self): self.events = []
        def emit(self, state, event, payload): self.events.append((event, payload))

    observer = Observer()
    planning = Pico.new_planning_state()
    state = WorkingState()
    state.set_goal("inspect")
    adapter = LegacyLoopStateAdapter(
        root=tmp_path, observer=observer,
    )
    adapter.bind_turn(
        working_state=state, planning=planning,
        watchdog=ProgressWatchdog(),
    )
    task_state = type("State", (), {"tool_steps": 1, "run_id": "state-unit"})()
    adapter.apply_tool_result(
        "read_file", {"path": "README.md", "start": 1, "end": 1},
        {"tool_status": "ok"}, "hello", task_state,
    )
    assert observer.events[0][0] == "working_state_updated"
    assert adapter.record_read_evidence(
        {"path": "README.md", "start": 1, "end": 1}, "hello", 1,
        freshness="v1", hint="hello",
    )
    assert planning["evidence_ledger"][0]["path"] == "readme.md"
    decision = adapter.observe_watchdog(
        task_state, "read_file", {"path": "README.md"},
        {"tool_status": "ok"}, "hello", 1,
    )
    assert decision.state
    assert adapter.adopt_protected_constraint(task_state, "use focused tests")
    assert adapter.protected_constraints == ["use focused tests"]
    assert [event for event, _ in observer.events].count("run_injected") == 1
    source = inspect.getsource(LegacyLoopStateAdapter)
    assert "self._runtime" not in source
    assert "LegacyLoopStateAdapter(runtime" not in source


def test_production_tool_turn_uses_loop_state_collaborator(tmp_path):
    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    agent = Pico(
        model_client=FakeModelClient([
            '<tool name="read_file" path="README.md" start="1" end="1"></tool>',
            "<final>done</final>",
        ]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="never",
    )
    delegate = agent.loop_state_collaborator
    calls = []

    class RecordingLoopState:
        def bind_turn(self, **kwargs): return delegate.bind_turn(**kwargs)
        def synchronize(self, **kwargs): return delegate.synchronize(**kwargs)
        def apply_tool_result(self, *args, **kwargs):
            calls.append("working_state")
            return delegate.apply_tool_result(*args, **kwargs)
        def record_read_evidence(self, *args, **kwargs):
            calls.append("evidence")
            return delegate.record_read_evidence(*args, **kwargs)
        def invalidate_evidence_for_paths(self, *args, **kwargs):
            return delegate.invalidate_evidence_for_paths(*args, **kwargs)
        def update_planning_state(self, *args, **kwargs):
            calls.append("planning")
            return delegate.update_planning_state(*args, **kwargs)
        def observe_watchdog(self, *args, **kwargs):
            calls.append("watchdog")
            return delegate.observe_watchdog(*args, **kwargs)

    agent.loop_state_collaborator = RecordingLoopState()
    assert agent.ask("read", run_id="loop-state-production") == "done"
    assert calls == ["evidence", "planning", "working_state", "watchdog"]
    events = [json.loads(line)["event"] for line in agent.run_store.trace_path(
        "loop-state-production"
    ).read_text(encoding="utf-8").splitlines()]
    assert events.count("working_state_updated") == 1


def test_model_invoker_preserves_streaming_and_nonstreaming_contracts():
    class Plain:
        def complete(self, prompt, max_tokens, **kwargs): return (prompt, max_tokens, kwargs)

    class Streaming:
        def stream_complete(self, prompt, max_tokens, on_delta=None, **kwargs):
            on_delta("delta")
            return (prompt, max_tokens, kwargs)

    assert LegacyModelInvoker(Plain(), 7).invoke_text("p", model_kwargs={"k": 1}) == ("p", 7, {"k": 1})
    deltas = []
    assert LegacyModelInvoker(Streaming(), 8).invoke_text("q", on_delta=deltas.append) == ("q", 8, {})
    assert deltas == ["delta"]


def test_model_invocation_metadata_is_returned_per_call_not_read_from_shared_state():
    class Client:
        def __init__(self): self.last_completion_metadata = {}
        def complete(self, prompt, max_tokens, **kwargs):
            self.last_completion_metadata = {"input_tokens": int(prompt)}
            return prompt

    invoker = LegacyModelInvoker(Client(), 1)
    first = invoker.invoke(protocol="legacy_text", prompt="1")
    second = invoker.invoke(protocol="legacy_text", prompt="2")
    assert first.completion_metadata == {"input_tokens": 1}
    assert second.completion_metadata == {"input_tokens": 2}


def test_concurrent_runtime_metadata_and_usage_stay_isolated(tmp_path):
    """Two real Runtime runs overlap; B returns before A after both have started."""
    barrier = threading.Barrier(2)
    allow_a_return = threading.Event()
    events = []
    events_lock = threading.Lock()

    class InterleavedClient:
        supports_prompt_cache = False

        def __init__(self, label, input_tokens, output_tokens, request_id):
            self.label = label
            self.input_tokens = input_tokens
            self.output_tokens = output_tokens
            self.request_id = request_id
            self._local = threading.local()

        @property
        def last_completion_metadata(self):
            return getattr(self._local, "metadata", {})

        @last_completion_metadata.setter
        def last_completion_metadata(self, value):
            self._local.metadata = value

        def complete(self, prompt, max_tokens, **kwargs):
            with events_lock:
                events.append(f"{self.label}:start")
            barrier.wait(timeout=5)
            if self.label == "A":
                assert allow_a_return.wait(timeout=5)
            self.last_completion_metadata = {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "request_id": self.request_id,
                "usage_record": {
                    "schema_version": 1,
                    "connection_profile_id": f"test-{self.label}",
                    "protocol": "test",
                    "request_id": self.request_id,
                    "context": {"actual_input_tokens": self.input_tokens},
                    "cache": {},
                    "output": {"output_tokens": self.output_tokens},
                    "cost": {},
                },
            }
            with events_lock:
                events.append(f"{self.label}:return")
            if self.label == "B":
                allow_a_return.set()
            return f"<final>{self.label}</final>"

    def make_agent(label, input_tokens, output_tokens, request_id):
        root = tmp_path / label
        root.mkdir()
        (root / "README.md").write_text(label, encoding="utf-8")
        return Pico(
            model_client=InterleavedClient(label, input_tokens, output_tokens, request_id),
            workspace=WorkspaceContext.build(root),
            session_store=SessionStore(root / ".codecub" / "sessions"),
            approval_policy="auto",
        )

    agent_a = make_agent("A", 101, 11, "request-A")
    agent_b = make_agent("B", 202, 22, "request-B")
    results = {}
    invocation_results = {}
    errors = []

    def record_invocation(label, agent):
        delegate = agent.model_invoker

        class RecordingInvoker:
            def invoke(self, **kwargs):
                result = delegate.invoke(**kwargs)
                invocation_results[label] = result
                return result

        agent.model_invoker = RecordingInvoker()

    record_invocation("A", agent_a)
    record_invocation("B", agent_b)

    def run(label, agent):
        try:
            results[label] = agent.ask(f"run {label}", run_id=f"run-{label}")
        except BaseException as exc:  # keep thread failures visible to pytest
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("A", agent_a)),
        threading.Thread(target=run, args=("B", agent_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert events.index("A:start") < events.index("B:return")
    assert events.index("B:start") < events.index("B:return") < events.index("A:return")
    assert results == {"A": "A", "B": "B"}
    assert invocation_results["A"].response == "<final>A</final>"
    assert invocation_results["A"].completion_metadata["request_id"] == "request-A"
    assert invocation_results["A"].completion_metadata["input_tokens"] == 101
    assert invocation_results["A"].completion_metadata["output_tokens"] == 11
    assert invocation_results["B"].response == "<final>B</final>"
    assert invocation_results["B"].completion_metadata["request_id"] == "request-B"
    assert invocation_results["B"].completion_metadata["input_tokens"] == 202
    assert invocation_results["B"].completion_metadata["output_tokens"] == 22
    assert agent_a.last_completion_metadata["request_id"] == "request-A"
    assert agent_b.last_completion_metadata["request_id"] == "request-B"
    assert agent_a.last_completion_metadata["input_tokens"] == 101
    assert agent_b.last_completion_metadata["input_tokens"] == 202

    records_a = agent_a.run_store.load_usage("run-A")
    records_b = agent_b.run_store.load_usage("run-B")
    assert len(records_a) == len(records_b) == 1
    assert records_a[0]["request_id"] == "request-A"
    assert records_a[0]["context"]["actual_input_tokens"] == 101
    assert records_a[0]["output"]["output_tokens"] == 11
    assert records_b[0]["request_id"] == "request-B"
    assert records_b[0]["context"]["actual_input_tokens"] == 202
    assert records_b[0]["output"]["output_tokens"] == 22
    assert "request-B" not in json.dumps(records_a)
    assert "request-A" not in json.dumps(records_b)

    usage_a = agent_a.usage_store.load_records(agent_a.session["id"])
    usage_b = agent_b.usage_store.load_records(agent_b.session["id"])
    assert len(usage_a) == len(usage_b) == 1
    assert usage_a[0]["request_id"] == "request-A"
    assert usage_a[0]["context"]["actual_input_tokens"] == 101
    assert usage_b[0]["request_id"] == "request-B"
    assert usage_b[0]["context"]["actual_input_tokens"] == 202
    assert "request-B" not in json.dumps(usage_a)
    assert "request-A" not in json.dumps(usage_b)


def test_native_production_path_invokes_model_collaborator_twice(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-spy"
        last_completion_metadata = {}

        def __init__(self):
            self.responses = [
                ModelResponse(tool_calls=(ToolCall("read-1", "read_file", {"path": "README.md"}),)),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
            return self.responses.pop(0)

    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    agent = Pico(
        model_client=NativeClient(),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
    )
    delegate = agent.model_invoker
    calls = []

    class RecordingInvoker:
        def invoke(self, **kwargs):
            calls.append(dict(kwargs))
            return delegate.invoke(**kwargs)

    agent.model_invoker = RecordingInvoker()
    assert agent.ask("read the file", run_id="native-two-call") == "Done."
    assert [call["protocol"] for call in calls] == ["native_tools", "native_tools"]

    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path("native-two-call").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    model_requests = [event for event in trace if event["event"] == "model_requested"]
    assert len(model_requests) == 2
    assert {event["run_id"] for event in model_requests} == {"native-two-call"}
    # Current trace contract exposes run_id only; turn_id lives in task_state
    # and no trace_id is emitted unless an upstream spine context supplies one.
    assert agent.run_store.load_task_state("native-two-call")["task_id"]
