from codecub.code_index import CodeIndex
import json

from codecub.retrieval import (
    CandidateLimits,
    FusionWeights,
    HybridRetriever,
    OpenAICompatibleReranker,
    RetrievalQuery,
    fuse_candidate_evidence,
    path_affinity_score,
    parse_retrieval_query,
    symbol_affinity_score,
)
from codecub.vector_index import CodeChunk


class CountingEmbeddings:
    model = "test-embedding"

    def __init__(self):
        self.calls = 0

    def embed(self, _text):
        self.calls += 1
        return [1.0, 0.0]


class RecordingReranker:
    model = "test-reranker"

    def __init__(self):
        self.calls = []

    def rerank(self, _query, documents):
        self.calls.append(list(documents))
        return documents


class StubVectorIndex:
    def __init__(self, chunks):
        self.chunks = chunks
        self.refresh_calls = 0

    def refresh(self, _embedding_client):
        self.refresh_calls += 1
        return {"chunks": len(self.chunks), "embedded": 0}

    def search(self, _vector, _limit):
        self.last_limit = _limit
        return [(chunk, 1.0) for chunk in self.chunks[:_limit]]


def _chunk(path="codecub/semantic.py", text="semantic result"):
    return CodeChunk(f"{path}:1:1", path, 1, 1, "", "hash", text)


def test_parse_retrieval_query_intents():
    assert parse_retrieval_query("where is run_tool defined") == RetrievalQuery(
        "where is run_tool defined", "definition", "run_tool", ""
    )
    assert parse_retrieval_query("class definition CodeIndex").kind == "class"
    assert parse_retrieval_query("class definition CodeIndex").symbol == "codeindex"
    function = parse_retrieval_query("function definition tool_patch_file")
    assert (function.intent, function.symbol, function.kind) == (
        "definition", "tool_patch_file", "function"
    )
    assert parse_retrieval_query("calls to append_trace").intent == "reference"
    assert parse_retrieval_query("calls to append_trace").symbol == "append_trace"
    assert parse_retrieval_query("where is append_trace called").intent == "reference"
    assert parse_retrieval_query("hybrid semantic retrieval architecture").intent == "generic"


def test_definition_ast_exact_is_prioritized_and_deduped_by_file(tmp_path):
    runtime = tmp_path / "codecub" / "runtime.py"
    runtime.parent.mkdir()
    runtime.write_text("def run_tool():\n    return 'ok'\n", encoding="utf-8")
    evaluation = tmp_path / "scripts" / "eval_retrieval.py"
    evaluation.parent.mkdir()
    evaluation.write_text(
        "# where is run_tool defined\n# benchmark helper\n", encoding="utf-8"
    )
    index = CodeIndex(tmp_path)
    index.refresh()

    result = HybridRetriever(tmp_path, index, embedding_client=False, reranker=False).retrieve(
        "where is run_tool defined", limit=5
    )

    assert result.strategy == "ast_exact_lexical"
    assert result.hits[0].path == "codecub/runtime.py"
    assert "ast_exact" in result.hits[0].sources
    assert len([hit for hit in result.hits if hit.path == "codecub/runtime.py"]) == 1


def test_reference_query_uses_ast_call_references(tmp_path):
    path = tmp_path / "codecub" / "tracing.py"
    path.parent.mkdir()
    path.write_text(
        "def append_trace():\n    pass\n\ndef caller():\n    append_trace()\n",
        encoding="utf-8",
    )
    index = CodeIndex(tmp_path)
    index.refresh()

    result = HybridRetriever(tmp_path, index, embedding_client=False, reranker=False).retrieve(
        "where is append_trace called", limit=5
    )

    assert result.strategy == "ast_reference_lexical"
    assert result.hits[0].path == "codecub/tracing.py"
    assert "ast_reference" in result.hits[0].sources
    assert result.hits[0].start_line <= 5 <= result.hits[0].end_line


def test_path_affinity_prefers_matching_module_tokens():
    query = "desktop app protocol"
    assert path_affinity_score(query, "codecub/app_protocol.py") > path_affinity_score(
        query, "codecub/cli.py"
    )


def test_path_affinity_combines_directory_and_filename_tokens():
    assert path_affinity_score(
        "usage telemetry aggregation", "codecub/telemetry/aggregation.py"
    ) == 2 / 3


def test_symbol_affinity_matches_camel_and_snake_case_symbols():
    assert symbol_affinity_score(
        "edit decision watchdog", "EditDecisionWatchdog"
    ) == 1.0
    assert symbol_affinity_score(
        "edit decision watchdog", "edit_decision"
    ) == 2 / 3


def test_final_fusion_preserves_multi_source_evidence_over_weak_rerank():
    weights = FusionWeights()
    strong_score, _ = fuse_candidate_evidence(
        {
            "path": "src/architecture.py",
            "symbol": "",
            "ranks": {"lexical": 1, "ast": 1, "semantic": 1, "rerank": 4},
        },
        "hybrid retrieval architecture",
        weights,
    )
    weak_score, _ = fuse_candidate_evidence(
        {
            "path": "src/unrelated.py",
            "symbol": "",
            "ranks": {"rerank": 1},
        },
        "hybrid retrieval architecture",
        weights,
    )
    assert strong_score > weak_score


def test_path_affinity_bonus_cannot_overwhelm_semantic_evidence():
    weights = FusionWeights()
    filename_only, _ = fuse_candidate_evidence(
        {
            "path": "src/desktop_app_protocol.py",
            "symbol": "",
            "ranks": {},
        },
        "desktop app protocol",
        weights,
    )
    semantic_match, _ = fuse_candidate_evidence(
        {
            "path": "src/implementation.py",
            "symbol": "",
            "ranks": {"semantic": 1},
        },
        "desktop app protocol",
        weights,
    )
    assert semantic_match > filename_only


def test_exact_definition_skips_embedding_and_reranker_with_metadata(tmp_path):
    path = tmp_path / "codecub" / "runtime.py"
    path.parent.mkdir()
    path.write_text("def run_tool():\n    return 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker)
    retriever.vector_index = StubVectorIndex([_chunk()])

    result = retriever.retrieve("where is run_tool defined")

    assert embeddings.calls == 0
    assert reranker.calls == []
    assert result.retrieval_route == "structural_fast_path"
    assert result.semantic_skipped_reason == "exact_definition"


def test_resolved_reference_skips_semantic(tmp_path):
    path = tmp_path / "codecub" / "runtime.py"
    path.parent.mkdir()
    path.write_text("def append_trace():\n    pass\n\ndef run():\n    append_trace()\n")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker)
    retriever.vector_index = StubVectorIndex([_chunk()])

    result = retriever.retrieve("calls to append_trace")

    assert embeddings.calls == 0
    assert reranker.calls == []
    assert result.retrieval_route == "structural_fast_path"
    assert result.semantic_skipped_reason == "resolved_reference"


def test_generic_and_low_confidence_structural_queries_use_semantic(tmp_path):
    path = tmp_path / "codecub" / "source.py"
    path.parent.mkdir()
    path.write_text("value = 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker)
    retriever.vector_index = StubVectorIndex([_chunk()])

    generic = retriever.retrieve("hybrid semantic retrieval architecture")
    missing = retriever.retrieve("where is missing_symbol defined")

    assert embeddings.calls == 2
    assert len(reranker.calls) == 2
    assert generic.retrieval_route == "full_semantic"
    assert missing.retrieval_route == "full_semantic"
    assert missing.semantic_skipped_reason == "none"
    assert generic.score_metadata["codecub/semantic.py"]["semantic_component"] > 0


def test_reranker_receives_only_configured_candidate_limit(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(
        tmp_path, index, embeddings, reranker, rerank_candidate_limit=3
    )
    retriever.vector_index = StubVectorIndex(
        [_chunk(f"codecub/result_{number}.py", f"result {number}") for number in range(10)]
    )

    retriever.retrieve("semantic architecture", limit=5)

    assert len(reranker.calls) == 1
    assert len(reranker.calls[0]) == 3


def test_semantic_recall_depth_and_protection_keep_late_affinity_candidate(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(
        tmp_path,
        index,
        embeddings,
        reranker,
        candidate_limits=CandidateLimits(semantic_recall=20),
    )
    chunks = [_chunk(f"codecub/result_{number}.py", f"result {number}") for number in range(14)]
    chunks.append(
        CodeChunk(
            "codecub/task_state.py:1:1",
            "codecub/task_state.py",
            1,
            1,
            "TaskCheckpointState",
            "task-state",
            "task checkpoint state",
        )
    )
    retriever.vector_index = StubVectorIndex(chunks)

    result = retriever.retrieve("task checkpoint state", limit=5)

    assert retriever.vector_index.last_limit == 20
    assert any(item["path"] == "codecub/task_state.py" for item in reranker.calls[0])
    debug = result.score_metadata["codecub/task_state.py"]
    assert debug["semantic_recall_rank"] == 15
    assert debug["protected"] is True
    assert debug["selected_for_rerank"] is True


def test_expanded_semantic_recall_keeps_single_embedding_and_rerank_request(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker)
    retriever.vector_index = StubVectorIndex(
        [_chunk(f"codecub/result_{number}.py") for number in range(20)]
    )

    retriever.retrieve("generic retrieval architecture")

    assert embeddings.calls == 1
    assert len(reranker.calls) == 1
    assert len(reranker.calls[0]) <= 8


def test_candidate_protection_preserves_multi_source_evidence(tmp_path):
    path = tmp_path / "codecub" / "architecture.py"
    path.parent.mkdir()
    path.write_text("generic retrieval architecture\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker, rerank_candidate_limit=1)
    retriever.vector_index = StubVectorIndex(
        [
            CodeChunk(
                "codecub/architecture.py:1:1",
                "codecub/architecture.py",
                1,
                1,
                "RetrievalArchitecture",
                "multi-source",
                "generic retrieval architecture",
            ),
            _chunk("codecub/other.py"),
        ]
    )

    result = retriever.retrieve("generic retrieval architecture")

    assert result.score_metadata["codecub/architecture.py"]["protected"] is True
    assert result.score_metadata["codecub/architecture.py"]["selected_for_rerank"] is True


def test_weak_semantic_recall_candidate_is_not_automatically_selected_for_rerank(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker, rerank_candidate_limit=1)
    chunks = [_chunk("codecub/high_signal.py", "generic retrieval architecture")]
    chunks.extend(_chunk(f"codecub/weak_{number}.py") for number in range(18))
    chunks.append(_chunk("codecub/weak_tail.py"))
    retriever.vector_index = StubVectorIndex(chunks)

    result = retriever.retrieve("generic retrieval architecture", limit=1)

    assert reranker.calls[0][0]["path"] == "codecub/high_signal.py"
    assert result.score_metadata["codecub/weak_tail.py"]["selected_for_rerank"] is False


def test_fusion_weights_remain_conservative_defaults():
    assert FusionWeights() == FusionWeights(
        lexical=0.7,
        ast=0.8,
        semantic=1.0,
        rerank=1.1,
        path_affinity=0.004,
        symbol_affinity=0.004,
    )


def test_generic_candidate_union_keeps_lexical_and_semantic_candidates(tmp_path):
    path = tmp_path / "codecub" / "lexical.py"
    path.parent.mkdir()
    path.write_text("desktop protocol implementation\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings, reranker = CountingEmbeddings(), RecordingReranker()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker)
    retriever.vector_index = StubVectorIndex([_chunk("codecub/semantic.py")])

    retriever.retrieve("desktop protocol")

    assert {item["path"] for item in reranker.calls[0]} == {
        "codecub/lexical.py",
        "codecub/semantic.py",
    }


def test_query_embedding_cache_uses_normalized_query(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    embeddings = CountingEmbeddings()
    retriever = HybridRetriever(tmp_path, index, embeddings, reranker=False)
    retriever.vector_index = StubVectorIndex([_chunk()])

    retriever.retrieve("hybrid semantic retrieval")
    retriever.retrieve("  hybrid   semantic retrieval  ")

    assert embeddings.calls == 1


def test_workspace_fingerprint_is_reused_and_invalidates_after_change(tmp_path):
    path = tmp_path / "source.py"
    path.write_text("value = 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    retriever = HybridRetriever(tmp_path, index, embedding_client=False, reranker=False)

    first = retriever._workspace_fingerprint(retriever._workspace_paths())
    second = retriever._workspace_fingerprint(retriever._workspace_paths())
    path.write_text("value = 2\n", encoding="utf-8")
    third = retriever._workspace_fingerprint(retriever._workspace_paths())

    assert first == second
    assert first != third
    assert retriever._workspace_hash_computations == 2


def test_http_reranker_sends_candidates_in_one_batch_request(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"results": []}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr("codecub.retrieval.urllib.request.urlopen", fake_urlopen)
    documents = [{"text": "one", "score": 1}, {"text": "two", "score": 0.5}]
    result = OpenAICompatibleReranker("https://example.invalid/reranks", "key", "model").rerank(
        "query", documents
    )

    assert result == documents
    assert len(requests) == 1
    assert json.loads(requests[0][0].data.decode())["documents"] == ["one", "two"]
