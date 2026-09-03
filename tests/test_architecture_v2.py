from collections import deque

from codecub.cache import (
    LocalJsonCache,
    embedding_cache_key,
    semantic_answer_cache_allowed,
)
from codecub.event_bus import AgentEvent, LocalEventBus, RedisEventBackplane
from codecub.run_queue import LocalRunQueue, RedisStreamRunQueue
from codecub.store_ports import SQLiteSessionStore
from codecub.worker import AgentRunWorker
from codecub.orchestration import Orchestrator
from codecub.models import FakeModelClient
from codecub.runtime import Pico, SessionStore
from codecub.workspace import WorkspaceContext
from codecub.code_index import CodeIndex
from codecub.vector_index import LocalVectorIndex, chunk_workspace
from codecub.retrieval import HybridRetriever
from codecub.experiments.ablations import validate_comparable, write_manifest
from codecub.spine import ControlMessage, InMemoryControlBus, RedisStreamControlBus
import threading
import time


def test_sqlite_session_store_round_trip(tmp_path):
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    store.save({"id": "run-1", "history": []})
    assert store.load("run-1")["id"] == "run-1"
    assert store.latest() == "run-1"


def test_local_run_queue_enforces_run_idempotency():
    queue = LocalRunQueue()
    run = {"run_id": "r1", "task": "inspect"}
    assert queue.enqueue(run)
    assert not queue.enqueue(run)
    assert queue.dequeue() == run
    queue.complete("r1")
    assert not queue.enqueue(run)


def test_redis_queue_claim_ack_fail_reclaim_and_dlq_contract():
    class Redis:
        def __init__(self):
            self.keys, self.streams, self.acks, self.acked = {}, {}, [], set()

        def xgroup_create(self, *args, **kwargs):
            del args, kwargs

        def set(self, key, value, nx=False):
            if nx and key in self.keys:
                return False
            self.keys[key] = value
            return True

        def xadd(self, stream, fields):
            entries = self.streams.setdefault(stream, [])
            message_id = f"{len(entries) + 1}-0"
            entries.append((message_id, fields))
            return message_id

        def xreadgroup(self, group, consumer, streams, count, block):
            del group, consumer, count, block
            stream = next(iter(streams))
            entries = [entry for entry in self.streams.get(stream, []) if (stream, entry[0]) not in self.acked]
            return [(stream, entries[:1])] if entries else []

        def xack(self, stream, group, message_id):
            self.acks.append((stream, group, message_id))
            self.acked.add((stream, message_id))

        def xautoclaim(self, stream, group, consumer, min_idle_ms, start, count):
            del group, consumer, min_idle_ms, start, count
            entries = [entry for entry in self.streams.get(stream, []) if (stream, entry[0]) not in self.acked]
            return ("0-0", entries[:1])

    redis = Redis()
    queue = RedisStreamRunQueue(redis, max_deliveries=1)
    assert queue.enqueue({"run_id": "redis-run", "task": "inspect", "trace_id": "trace"})
    claimed = queue.dequeue()
    assert claimed["trace_id"] == "trace"
    queue.complete("redis-run")
    assert redis.acks
    assert queue.reclaim_stale_pending() is None
    second = RedisStreamRunQueue(redis, stream="codecub:retry", max_deliveries=1)
    assert second.enqueue({"run_id": "retry-run", "task": "inspect"})
    assert second.dequeue()["run_id"] == "retry-run"
    assert second.dequeue() is None
    assert redis.streams[second.dead_letter_stream]
    retry = RedisStreamRunQueue(redis, stream="codecub:retry-on-fail", max_deliveries=2)
    assert retry.enqueue({"run_id": "retry-on-fail", "task": "inspect"})
    assert retry.dequeue()["run_id"] == "retry-on-fail"
    retry.fail("retry-on-fail", RuntimeError("transient"))
    assert retry.reclaim_stale_pending()["run_id"] == "retry-on-fail"
    retry.complete("retry-on-fail")
    assert redis.keys["codecub:retry-on-fail:run:retry-on-fail"] == "completed"
    crash = RedisStreamRunQueue(redis, stream="codecub:crash", max_deliveries=2)
    assert crash.enqueue({"run_id": "crash-run", "task": "inspect"})
    assert crash.dequeue()["run_id"] == "crash-run"
    assert crash.reclaim_stale_pending()["run_id"] == "crash-run"
    assert crash.reclaim_stale_pending() is None
    assert redis.streams[crash.dead_letter_stream]


def test_worker_executes_whole_run_once():
    class Agent:
        def ask(self, task, run_id):
            return f"{run_id}:{task}"

    queue = LocalRunQueue()
    queue.enqueue({"run_id": "r2", "task": "inspect"})
    result = AgentRunWorker(queue, lambda _run: Agent()).run_once()
    assert result == {"run_id": "r2", "status": "completed", "answer": "r2:inspect"}
    assert queue.statuses["r2"] == "completed"


def test_worker_applies_distributed_injection_once_to_running_agent():
    started = threading.Event()
    received = []

    class Agent:
        def ask(self, task, run_id):
            del task, run_id
            started.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                received.extend(self.injection_provider())
                if received:
                    return received[0]
                time.sleep(0.01)
            return "missing"

    queue = LocalRunQueue()
    queue.enqueue({"run_id": "r-control", "task": "inspect"})
    worker = AgentRunWorker(queue, lambda _run: Agent(), control_bus=InMemoryControlBus())
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    assert started.wait(1)
    control = ControlMessage("RUN_INJECT", "r-control", payload={"message": "constraint"})
    assert worker.apply_control(control)
    assert not worker.apply_control(control)
    thread.join(timeout=1)
    assert result == [{"run_id": "r-control", "status": "completed", "answer": "constraint"}]


def test_worker_records_control_with_explicit_correlation_tuple():
    events = []

    class Agent:
        current_task_state = type("State", (), {"run_id": "r-trace"})()

        def emit_trace(self, task_state, event, payload):
            assert task_state is self.current_task_state
            events.append((event, payload))

    worker = AgentRunWorker(LocalRunQueue(), lambda _run: Agent())
    agent = Agent()
    worker._active_agents["r-trace"] = agent
    worker._mailboxes["r-trace"] = deque()
    control = ControlMessage("RUN_INJECT", "r-trace", "turn", "session", "conversation", "trace", {"message": "only once"})
    assert worker.apply_control(control)
    assert worker._drain_mailbox("r-trace") == ["only once"]
    assert events == [("control_applied", {
        "control_id": control.control_id, "control_type": "RUN_INJECT", "trace_id": "trace",
        "session_id": "session", "conversation_id": "conversation", "turn_id": "turn", "run_id": "r-trace",
    })]


def test_worker_cancel_control_reaches_running_agent_before_terminal_status():
    started = threading.Event()

    class Agent:
        def ask(self, task, run_id):
            del task
            started.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if self.cancel_checker(self, type("State", (), {"run_id": run_id})()):
                    return "cancelled by worker"
                time.sleep(0.01)
            return "not cancelled"

    queue = LocalRunQueue()
    queue.enqueue({"run_id": "r-cancel", "task": "inspect"})
    worker = AgentRunWorker(queue, lambda _run: Agent(), control_bus=InMemoryControlBus())
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    assert started.wait(1)
    assert worker.apply_control(ControlMessage("RUN_CANCEL", "r-cancel"))
    thread.join(timeout=1)
    assert result == [{"run_id": "r-cancel", "status": "cancelled", "answer": "cancelled by worker"}]


def test_worker_acks_a_cancelled_agent_that_exits_by_exception():
    started = threading.Event()

    class Agent:
        def ask(self, task, run_id):
            del task
            started.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if self.cancel_checker(self, type("State", (), {"run_id": run_id})()):
                    raise RuntimeError("cancelled")
                time.sleep(0.01)
            raise AssertionError("cancel control was not received")

    queue = LocalRunQueue()
    queue.enqueue({"run_id": "r-cancel-error", "task": "inspect"})
    worker = AgentRunWorker(queue, lambda _run: Agent(), control_bus=InMemoryControlBus())
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    assert started.wait(1)
    assert worker.apply_control(ControlMessage("RUN_CANCEL", "r-cancel-error"))
    thread.join(timeout=1)
    assert result == [{"run_id": "r-cancel-error", "status": "cancelled", "answer": "", "error": "cancelled"}]
    assert queue.statuses["r-cancel-error"] == "completed"


def test_worker_automatically_consumes_distributed_redis_injection():
    started = threading.Event()

    class Redis:
        def __init__(self):
            self.entries, self.keys, self.acks, self.delivered = [], {}, [], False

        def xgroup_create(self, *args, **kwargs):
            del args, kwargs

        def xadd(self, stream, fields):
            self.entries.append(("1-0", fields))
            return "1-0"

        def xreadgroup(self, group, consumer, streams, count, block):
            del group, consumer, streams, count, block
            if self.delivered or not self.entries:
                return []
            self.delivered = True
            return [("codecub:controls", self.entries)]

        def xack(self, stream, group, message_id):
            self.acks.append((stream, group, message_id))

        def set(self, key, value, nx=False):
            del value
            if nx and key in self.keys:
                return False
            self.keys[key] = "1"
            return True

    class Agent:
        def ask(self, task, run_id):
            del task, run_id
            started.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                injected = self.injection_provider()
                if injected:
                    return injected[0]
                time.sleep(0.01)
            return "missing"

    queue = LocalRunQueue()
    queue.enqueue({"run_id": "r-redis-control", "task": "inspect"})
    redis = Redis()
    bus = RedisStreamControlBus(redis)
    worker = AgentRunWorker(queue, lambda _run: Agent(), control_bus=bus)
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    assert started.wait(1)
    bus.publish(ControlMessage("RUN_INJECT", "r-redis-control", payload={"message": "constraint"}))
    thread.join(timeout=1)
    assert result == [{"run_id": "r-redis-control", "status": "completed", "answer": "constraint"}]
    assert redis.acks == [("codecub:controls", "codecub-controls:worker-1", "1-0")]


def test_cache_keys_are_stable_and_semantic_answer_is_guarded(tmp_path):
    cache = LocalJsonCache(tmp_path / "cache.json")
    key = embedding_cache_key("model", "text")
    cache.set(key, [1.0])
    assert cache.get(key) == [1.0]
    assert semantic_answer_cache_allowed(True, False, False)
    assert not semantic_answer_cache_allowed(False, False, False)
    assert not semantic_answer_cache_allowed(True, True, False)
    assert not semantic_answer_cache_allowed(True, False, True)


def test_redis_backplane_drops_own_events():
    class Redis:
        def __init__(self):
            self.messages = []

        def publish(self, _channel, message):
            self.messages.append(message)

    local = LocalEventBus()
    received = []
    local.subscribe(received.append)
    redis = Redis()
    backplane = RedisEventBackplane(redis, origin="origin", local=local)
    event = AgentEvent("e1", "run.started", "now", "r1", "a1", {})
    backplane.publish(event)
    assert received == [event]
    assert backplane.consume(redis.messages[0]) is None
    assert received == [event]


def test_orchestrator_isolates_read_only_research_and_blocks_parallel_implement(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    parent = Pico(
        model_client=FakeModelClient(["<final>found</final>"],),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        max_steps=2,
        max_depth=1,
    )
    result = Orchestrator(parent).dispatch("research", "Locate README", 1)
    assert result.role == "research"
    assert result.status == "completed"
    assert parent.session["history"] == []
    try:
        Orchestrator(parent).dispatch_many([("implement", "edit", 1)])
    except ValueError as exc:
        assert "parallel implementation" in str(exc)
    else:
        raise AssertionError("parallel implement must be rejected")


def test_orchestrator_propagates_parent_cancellation_to_subagent(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    parent = Pico(
        model_client=FakeModelClient(["<final>must not run</final>"]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        max_steps=2,
        max_depth=1,
    )
    parent.cancel_checker = lambda _runtime, _state: True
    result = Orchestrator(parent).dispatch("research", "Locate README", 1)
    assert result.status == "cancelled"
    assert "canceled" in result.answer.lower()


def test_vector_index_reuses_unchanged_chunks_and_reembeds_changed_chunks(tmp_path):
    path = tmp_path / "module.py"
    path.write_text("def target():\n    return 1\n", encoding="utf-8")

    class Embeddings:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        def embed(self, text):
            self.calls += 1
            return [float(len(text)), 1.0]

    code_index = CodeIndex(tmp_path)
    code_index.refresh()
    index = LocalVectorIndex(tmp_path, lambda: chunk_workspace(tmp_path, code_index))
    client = Embeddings()
    first = index.refresh(client)
    assert first["embedded"] == 1
    second = index.refresh(client)
    assert second["embedded"] == 0
    path.write_text("def target():\n    return 2\n", encoding="utf-8")
    code_index.refresh()
    third = index.refresh(client)
    assert third["embedded"] == 1


def test_retrieval_cache_invalidates_when_workspace_changes(tmp_path):
    path = tmp_path / "example.py"
    path.write_text("def target():\n    return 1\n", encoding="utf-8")
    code_index = CodeIndex(tmp_path)
    code_index.refresh()
    retriever = HybridRetriever(tmp_path, code_index, embedding_client=False, reranker=False)
    first = retriever.retrieve("target")
    second = retriever.retrieve("target")
    assert not first.cache_hit
    assert second.cache_hit
    path.write_text("def target():\n    return 2\n", encoding="utf-8")
    code_index.refresh()
    third = retriever.retrieve("target")
    assert not third.cache_hit


def test_ablation_manifest_enforces_comparable_settings(tmp_path):
    rows = write_manifest(
        tmp_path / "ablations.json", "provider", "model", "tasks", 8, 0.2, 0.9
    )
    assert len(rows) == 9
    assert validate_comparable(rows)
    changed = [dict(row) for row in rows]
    changed[1]["model"] = "other"
    try:
        validate_comparable(changed)
    except ValueError:
        pass
    else:
        raise AssertionError("incomparable ablation manifest must be rejected")
