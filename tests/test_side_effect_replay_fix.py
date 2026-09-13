"""Regression tests for the side-effect replay protection corrections."""

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

from codecub import MiniAgent, SessionStore, WorkspaceContext
from codecub.models import ModelResponse
from codecub.run_store import RunStore
from codecub.task_state import TaskState


def _claim_process_worker(run_root, run_id, ready, results):
    ready.wait(30)
    store = RunStore(run_root)
    state = TaskState.from_dict(store.load_task_state(run_id))
    results.put(
        store.claim_side_effect_operation(
            state, "operation-shared", "append_effect", "digest-same"
        )
    )


class _LegacyTextClient:
    supports_native_tools = True
    supports_prompt_cache = False
    model = "legacy-recovery-test"
    last_completion_metadata = {}

    def __init__(self, responses):
        self.responses = list(responses)

    def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
        del messages, tools, max_new_tokens, tool_choice
        return self.responses.pop(0)


def _legacy_agent(tmp_path, responses):
    return MiniAgent(
        model_client=_LegacyTextClient(responses),
        workspace=WorkspaceContext.build(tmp_path, repo_root_override=tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        allowed_tools=("write_file",),
    )


@pytest.mark.parametrize("event_count", [1000, 5000, 10000])
def test_run_store_concurrent_trace_append_is_complete(tmp_path, event_count):
    store = RunStore(tmp_path / ".codecub" / "runs")
    state = TaskState.create(
        run_id=f"trace-{event_count}",
        task_id=f"task-trace-{event_count}",
        user_request="append trace concurrently",
    )
    store.start_run(state)

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(
            pool.map(
                lambda index: store.append_trace(
                    state, {"event_id": f"event-{index:05d}"}
                ),
                range(event_count),
            )
        )

    lines = store.trace_path(state.run_id).read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    event_ids = [event["event_id"] for event in events]
    assert len(events) == event_count
    assert len(set(event_ids)) == event_count
    assert set(event_ids) == {f"event-{index:05d}" for index in range(event_count)}


def test_run_store_multiprocess_claim_has_one_winner(tmp_path):
    store = RunStore(tmp_path / ".codecub" / "runs")
    state = TaskState.create(
        run_id="multiprocess-claim",
        task_id="task-multiprocess-claim",
        user_request="claim one operation from many processes",
    )
    store.start_run(state)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_claim_process_worker,
            args=(str(store.root), state.run_id, ready, results),
        )
        for _ in range(8)
    ]
    for process in processes:
        process.start()
    ready.set()
    for process in processes:
        process.join(60)

    assert all(process.exitcode == 0 for process in processes)
    claims = [results.get(timeout=5) for _ in processes]
    assert sum(bool(item["claimed"]) for item in claims) == 1
    assert sum(not bool(item["claimed"]) for item in claims) == 7


def test_legacy_recovery_call_id_is_deterministic_with_identity(tmp_path):
    agent = _legacy_agent(tmp_path, [])
    content = '<tool>{"name":"write_file","args":{"path":"x.txt","content":"x"}}</tool>'

    first, first_reason = agent._recover_native_text_tool_call(
        content, recovery_identity="assistant-attempt:1:recovered-ordinal:0"
    )
    second, second_reason = agent._recover_native_text_tool_call(
        content, recovery_identity="assistant-attempt:1:recovered-ordinal:0"
    )

    assert first_reason is None and second_reason is None
    assert first is not None and second is not None
    assert first.id == second.id


def test_legacy_operation_identity_persists_and_distinguishes_attempts(tmp_path):
    store = RunStore(tmp_path / ".codecub" / "runs")
    state = TaskState.create(
        run_id="legacy-identity",
        task_id="task-legacy-identity",
        user_request="persist legacy operation identities",
    )
    store.start_run(state)

    first = store.ensure_legacy_operation_identity(state, 1, 0, "write_file")
    second = store.ensure_legacy_operation_identity(state, 2, 0, "write_file")
    restored = TaskState.from_dict(store.load_task_state(state.run_id))
    repeated = store.ensure_legacy_operation_identity(restored, 1, 0, "write_file")

    assert first == repeated
    assert first["operation_key"] != second["operation_key"]
    assert "args" not in first["operation_key"]
    assert len(restored.legacy_operation_identities) == 2


def test_legacy_recovered_side_effect_replay_is_blocked_after_restart(tmp_path):
    legacy_call = '<tool>{"name":"write_file","args":{"path":"once.txt","content":"once"}}</tool>'
    first = _legacy_agent(tmp_path, [ModelResponse(text=legacy_call), ModelResponse(text="Done.")])
    assert first.ask("write once", run_id="legacy-replay") == "Done."

    second = _legacy_agent(tmp_path, [ModelResponse(text=legacy_call), ModelResponse(text="Done.")])
    assert second.ask("write once", run_id="legacy-replay") == "Done."
    assert (tmp_path / "once.txt").read_text(encoding="utf-8") == "once"

    state = second.run_store.load_task_state("legacy-replay")
    assert len(state["side_effect_operations"]) == 1
    trace = [
        json.loads(line)
        for line in second.run_store.trace_path("legacy-replay")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    tool_events = [
        event
        for event in trace
        if event.get("event") == "tool_executed"
        and event.get("tool_call_source") == "legacy_recovered"
    ]
    assert len(tool_events) == 2
    assert tool_events[0]["side_effect_operation_key"] == tool_events[1][
        "side_effect_operation_key"
    ]
    assert tool_events[1]["tool_error_code"] == "side_effect_replay_blocked"


def test_legacy_recovered_args_change_is_conflict(tmp_path):
    first_call = '<tool>{"name":"write_file","args":{"path":"conflict.txt","content":"one"}}</tool>'
    second_call = '<tool>{"name":"write_file","args":{"path":"conflict.txt","content":"two"}}</tool>'
    first = _legacy_agent(tmp_path, [ModelResponse(text=first_call), ModelResponse(text="Done.")])
    assert first.ask("write", run_id="legacy-conflict") == "Done."

    second = _legacy_agent(tmp_path, [ModelResponse(text=second_call), ModelResponse(text="Done.")])
    assert second.ask("write", run_id="legacy-conflict") == "Done."
    assert (tmp_path / "conflict.txt").read_text(encoding="utf-8") == "one"

    trace = [
        json.loads(line)
        for line in second.run_store.trace_path("legacy-conflict")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    conflict_events = [
        event
        for event in trace
        if event.get("event") == "tool_executed"
        and event.get("tool_call_source") == "legacy_recovered"
    ]
    assert conflict_events[-1]["tool_error_code"] == "idempotency_key_conflict"
