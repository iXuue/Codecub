from codecub.cache import (
    LocalJsonCache,
    embedding_cache_key,
    semantic_answer_cache_allowed,
)
from codecub.event_bus import AgentEvent, LocalEventBus, RedisEventBackplane
from codecub.run_queue import LocalRunQueue
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


def test_worker_executes_whole_run_once():
    class Agent:
        def ask(self, task, run_id):
            return f"{run_id}:{task}"

    queue = LocalRunQueue()
    queue.enqueue({"run_id": "r2", "task": "inspect"})
    result = AgentRunWorker(queue, lambda _run: Agent()).run_once()
    assert result == {"run_id": "r2", "status": "completed", "answer": "r2:inspect"}
    assert queue.statuses["r2"] == "completed"


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
