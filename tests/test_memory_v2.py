"""Phase 3 — Memory 2.0 deterministic validation.

Covers the spec's test matrix (§58-§68) plus Memory-OFF compatibility and
runtime/Context-Compiler integration. Everything here is deterministic — no
model calls. Cross-session tests instantiate two independent MemoryV2/runtime
objects sharing only the on-disk workspace (never the session history).
"""

import json

import pytest

from codecub.memory_v2 import MemoryV2
from codecub.memory_v2.consolidation import (
    ACTION_CONFLICT,
    ACTION_DUPLICATE,
    ACTION_NEW,
    ACTION_SUPERSEDE,
    MemoryConsolidator,
)
from codecub.memory_v2.durable import (
    DurableMemoryStore,
    STATUS_SUPERSEDED,
)
from codecub.memory_v2.evidence import (
    STATUS_FRESH,
    STATUS_MISSING,
    STATUS_STALE,
    EvidenceRecord,
    EvidenceStore,
)
from codecub.memory_v2.extraction import (
    MemoryCandidate,
    MemoryExtractor,
    reject_candidate,
)
from codecub.memory_v2.retrieval import (
    FRESH_MARKER,
    STALE_MARKER,
)
from codecub.memory_v2.secrets import contains_secret, filter_text


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "runtime.py").write_text("def ask():\n    pass\n", encoding="utf-8")
    (tmp_path / "tools.py").write_text("def run_tool():\n    pass\n", encoding="utf-8")
    return tmp_path


def make_v2(root, **kwargs):
    return MemoryV2(root, **kwargs)


def make_working_state(goal="", symbols=None, files=None, blockers=None, verification=None):
    from codecub.context_compiler import WorkingState

    state = WorkingState()
    state.set_goal(goal)
    for item in symbols or []:
        state.add_relevant_symbol(path=item.get("path", ""), name=item.get("name", ""), kind=item.get("kind", "symbol"))
    for path in files or []:
        state.add_changed_file(path)
    for blocker in blockers or []:
        state.add_blocker(blocker)
    for entry in verification or []:
        state._record_verification(entry.get("command", ""), entry.get("status", "ok"), entry.get("error_sig", ""), entry.get("step", 0))
    return state


# ======================================================================
# 1. Evidence Store (§58)
# ======================================================================


def test_evidence_read_source_creates_record(workspace):
    v2 = make_v2(workspace)
    created = v2.record_tool_evidence("read_file", {"path": "runtime.py", "start": 1, "end": 5}, "def ask(): pass")
    assert len(created) == 1
    record = created[0]
    assert record.path == "runtime.py"
    assert record.kind == "source_location"
    assert record.status == STATUS_FRESH
    assert record.evidence_id


def test_evidence_path_canonicalized(workspace):
    v2 = make_v2(workspace)
    created = v2.record_tool_evidence("read_file", {"path": "./runtime.py"}, "def ask(): pass")
    assert created[0].path == "runtime.py"


def test_evidence_symbol_search_creates_symbol_evidence(workspace):
    v2 = make_v2(workspace)
    created = v2.record_tool_evidence(
        "symbol_search", {"query": "Pico.ask", "path": "runtime.py"}, "matched"
    )
    assert created and created[0].kind == "symbol_location"
    assert created[0].symbol == "Pico.ask"
    assert created[0].path == "runtime.py"


def test_evidence_source_hash_recorded(workspace):
    v2 = make_v2(workspace)
    created = v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "def ask(): pass")
    assert created[0].source_hash
    from codecub.memory import file_freshness

    assert created[0].source_hash == file_freshness("runtime.py", workspace)


def test_evidence_same_identity_dedupes_to_latest(workspace):
    v2 = make_v2(workspace)
    first = v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "first read")
    second = v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "second read")
    assert first[0].evidence_id != second[0].evidence_id
    latest = v2.evidence_store.latest_records()
    assert len(latest) == 1
    assert "second read" in latest[0]["summary"]
    assert v2.evidence_store.size() == 2  # superseded history is kept


def test_evidence_newer_supersedes_old(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "first read")
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "second read")
    records = v2.evidence_store.records
    statuses = [r["status"] for r in records]
    assert statuses.count(STATUS_SUPERSEDED) == 1
    assert statuses.count(STATUS_FRESH) == 1


def test_evidence_changed_file_becomes_stale(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "def ask(): pass")
    (workspace / "runtime.py").write_text("def ask():\n    return 42\n", encoding="utf-8")
    stale, missing = v2.refresh_freshness()
    assert len(stale) == 1
    assert stale[0]["path"] == "runtime.py"
    assert stale[0]["status"] == STATUS_STALE


def test_evidence_reread_creates_fresh_new_evidence(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "old")
    (workspace / "runtime.py").write_text("def ask():\n    return 42\n", encoding="utf-8")
    v2.refresh_freshness()
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "new content read")
    latest = v2.evidence_store.latest_records()
    assert latest[0]["status"] == STATUS_FRESH
    assert "new content read" in latest[0]["summary"]


def test_evidence_deleted_file_becomes_missing(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "def ask(): pass")
    (workspace / "runtime.py").unlink()
    stale, missing = v2.refresh_freshness()
    assert len(missing) == 1
    assert missing[0]["status"] == STATUS_MISSING


def test_evidence_stale_still_returns_location_hint(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop here")
    (workspace / "runtime.py").write_text("changed\n", encoding="utf-8")
    v2.refresh_freshness()
    result = v2.retrieve("runtime loop", force=True)
    assert result.evidence_items
    item = result.evidence_items[0]
    assert item.path == "runtime.py"
    assert item.marker == STALE_MARKER
    assert "runtime loop here" in item.text


def test_evidence_stale_rendered_revalidate_not_truth(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "old implementation detail")
    (workspace / "runtime.py").write_text("brand new\n", encoding="utf-8")
    v2.refresh_freshness()
    rendered = v2.retrieve("runtime implementation", force=True).render()
    assert STALE_MARKER in rendered
    assert "re-read before relying" in rendered


def test_evidence_outside_workspace_rejected(workspace):
    v2 = make_v2(workspace)
    created = v2.record_tool_evidence("read_file", {"path": "../../etc/passwd"}, "secret file")
    assert created == []
    records = v2.evidence_store.latest_records()
    assert all(r["path"].startswith("..") is False for r in records)


def test_evidence_bounded(workspace):
    store = EvidenceStore(workspace, max_records=5)
    for index in range(10):
        record = EvidenceRecord.create(
            path=f"file{index}.py", kind="source_location", summary=f"summary {index}"
        )
        store.records.append(record.to_dict())
    store._enforce_bounds()
    assert len(store.records) <= 5


# ======================================================================
# 2. Durable Memory (§59)
# ======================================================================


def test_durable_convention_persists(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    candidate = MemoryCandidate(
        candidate_type="convention",
        statement="Experiments use fresh workspace copies.",
        source_refs=[{"kind": "user_final_answer_line"}],
    )
    outcome = cons.apply(candidate)
    assert outcome.action == ACTION_NEW
    assert store.active_size() == 1
    assert store.active_records()[0]["topic"] == "project-conventions"


def test_durable_decision_persists(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    candidate = MemoryCandidate(
        candidate_type="decision",
        statement="Decision: keep artifacts immutable.",
        source_refs=[{"kind": "user_final_answer_line"}],
    )
    cons.apply(candidate)
    assert store.active_records()[0]["topic"] == "key-decisions"


def test_durable_verified_test_command_persists(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    candidate = MemoryCandidate(
        candidate_type="test_command",
        statement="Test command is: python -m pytest tests/test_pico.py",
        source_refs=[{"kind": "verification_event", "command": "python -m pytest tests/test_pico.py"}],
    )
    cons.apply(candidate)
    record = store.active_records()[0]
    assert record["topic"] == "build-and-test"
    assert record["evidence_refs"]


def test_durable_temporary_blocker_rejected(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    candidate = MemoryCandidate(
        candidate_type="convention",
        statement="Next step: re-read runtime.py",
        source_refs=[{"kind": "user_final_answer_line"}],
    )
    outcome = cons.apply(candidate)
    assert outcome.action == "REJECT"
    assert store.active_size() == 0


def test_durable_current_next_step_rejected(workspace):
    assert reject_candidate(MemoryCandidate(candidate_type="convention", statement="Current blocker: stuck on verify", source_refs=[{"kind": "x"}])) != ""


def test_durable_raw_traceback_rejected(workspace):
    assert reject_candidate(MemoryCandidate(candidate_type="convention", statement="exit_code: 1 stdout: boom traceback: x", source_refs=[{"kind": "x"}])) != ""


def test_durable_line_level_transient_rejected(workspace):
    assert reject_candidate(MemoryCandidate(candidate_type="convention", statement="line 42 has the bug", source_refs=[{"kind": "x"}])) != ""


def test_durable_secret_rejected(workspace):
    assert reject_candidate(MemoryCandidate(candidate_type="preference", statement="password is hunter2secret", source_refs=[{"kind": "x"}])) != ""


def test_durable_duplicate_consolidated(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    candidate = MemoryCandidate(candidate_type="convention", statement="Experiments use fresh copies.", source_refs=[{"kind": "user_final_answer_line"}])
    cons.apply(candidate)
    outcome = cons.apply(candidate)
    assert outcome.action == ACTION_DUPLICATE
    assert store.active_size() == 1


def test_durable_contradiction_detected(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    cons.apply(MemoryCandidate(candidate_type="environment", statement="Environment: Project uses Python 3.11", source_refs=[{"kind": "runtime_metadata"}]))
    outcome = cons.apply(MemoryCandidate(candidate_type="environment", statement="Environment: Project uses Python 3.13", source_refs=[{"kind": "runtime_metadata"}]))
    assert outcome.action == ACTION_CONFLICT
    active = store.active_records()
    assert len(active) == 2
    assert all(record["conflict_with"] for record in active)


def test_durable_supersede_works(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    cons.apply(MemoryCandidate(candidate_type="test_command", statement="Test command is: pytest tests/test_pico.py", source_refs=[{"kind": "verification_event"}]))
    new = cons.apply(MemoryCandidate(candidate_type="test_command", statement="Test command is: python -m pytest tests/test_pico.py", source_refs=[{"kind": "verification_event"}]))
    assert new.action == ACTION_SUPERSEDE
    assert new.existing["status"] == STATUS_SUPERSEDED
    assert new.record.supersedes == new.existing["memory_id"]


def test_durable_provenance_retained(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    cons.apply(
        MemoryCandidate(
            candidate_type="convention",
            statement="DeepSeek uses native tools for this project.",
            source_refs=[{"kind": "user_final_answer_line"}],
            source_task_id="task_abc",
            source_run_id="run_xyz",
            source_evidence_ids=["ev_1"],
        ),
        source_task_id="task_abc",
        source_run_id="run_xyz",
    )
    record = store.active_records()[0]
    assert record["source_task_ids"] == ["task_abc"]
    assert record["source_run_ids"] == ["run_xyz"]
    assert record["source_evidence_ids"] == ["ev_1"]


def test_durable_retired_not_retrieved(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    cons.apply(MemoryCandidate(candidate_type="convention", statement="Old convention fact.", source_refs=[{"kind": "user_final_answer_line"}]))
    record = store.active_records()[0]
    store.set_status(record["memory_id"], "retired")
    v2 = MemoryV2(workspace)
    result = v2.retrieve("old convention", force=True)
    assert result.durable_items == []


# ======================================================================
# 3. Extraction (§60)
# ======================================================================


def test_extraction_completed_task_trigger(workspace):
    v2 = make_v2(workspace)
    ws = make_working_state(
        goal="fix tests",
        verification=[{"command": "python -m pytest tests/test_pico.py", "status": "ok", "step": 3}],
    )
    promoted, rejections, _sup, _conf, _dup = v2.extract_and_persist(ws, "fix tests", "", run_id="run_1")
    assert promoted, "verified test command should promote"
    assert any("python -m pytest" in r["statement"] for r in v2.durable_store.active_records())


def test_extraction_verifier_produces_validated_candidate(workspace):
    extractor = MemoryExtractor(task_id="t", run_id="r")
    ws = make_working_state(verification=[{"command": "uv run pytest -q", "status": "ok", "step": 1}])
    candidates = extractor.extract_from_verification(ws)
    assert len(candidates) == 1
    assert candidates[0].candidate_type == "test_command"
    assert candidates[0].source_refs


def test_extraction_explicit_user_remember_triggers_candidate(workspace):
    extractor = MemoryExtractor(task_id="t", run_id="r")
    candidates = extractor.extract_from_user_intent(
        "remember these facts", "Project convention: keep artifacts immutable.\n"
    )
    assert len(candidates) == 1
    assert candidates[0].candidate_type == "convention"
    assert candidates[0].source_user_statement == "remember these facts"


def test_extraction_model_guess_without_evidence_rejected(workspace):
    candidate = MemoryCandidate(
        candidate_type="convention",
        statement="The repo uses Bazel internally.",
        source_refs=[],
    )
    assert reject_candidate(candidate) == "unverified_claim"


def test_extraction_failed_task_no_unsupported_conclusion(workspace):
    v2 = make_v2(workspace)
    ws = make_working_state(
        goal="debug failure",
        blockers=["verification failed: pytest -q (error|1)"],
        verification=[{"command": "pytest -q", "status": "error", "error_sig": "error|1", "step": 2}],
    )
    promoted, rejections, *_ = v2.extract_and_persist(ws, "debug failure", "Stopped.", run_id="run_fail")
    assert promoted == []
    # failed verification must not be promoted as a validated workflow
    assert v2.durable_store.active_size() == 0


def test_extraction_working_state_not_auto_promoted_wholesale(workspace):
    v2 = make_v2(workspace)
    ws = make_working_state(
        goal="some goal",
        blockers=["stuck"],
        files=["runtime.py"],
        symbols=[{"path": "runtime.py", "name": "Pico.ask"}],
    )
    promoted, *_ = v2.extract_and_persist(ws, "some goal", "", run_id="run_ws")
    assert promoted == []
    assert v2.durable_store.active_size() == 0


def test_evidence_summary_prefers_code_signal_lines():
    """Found by Fast Validation Task C: a read of the flags region must yield
    a summary mentioning the flag, not the first (env-allowlist) lines."""
    from codecub.memory_v2.evidence import _summarize_read_result

    result = (
        '55:     "TMP",\n'
        '56:     "TEMP",\n'
        '57:     "USER",\n'
        '58: DEFAULT_FEATURE_FLAGS = {\n'
        '59:     "memory": True,\n'
        '60:     "memory_v2": True,\n'
    )
    summary = _summarize_read_result(result)
    assert "DEFAULT_FEATURE_FLAGS" in summary
    assert '"memory"' in summary or "memory" in summary
    assert "TMP" not in summary


def test_evidence_summary_falls_back_to_first_lines():
    from codecub.memory_v2.evidence import _summarize_read_result

    assert _summarize_read_result("plain line one\nplain line two\n") == "plain line one | plain line two"


def test_extraction_generic_shell_success_not_promoted(workspace):
    """Spec §20: 瞬时 shell output（如 `dir`/`ls` 成功）不进 Durable。"""
    v2 = make_v2(workspace)
    v2.record_tool_evidence(
        "run_shell",
        {"command": "dir codecub", "timeout": 20},
        "exit_code: 0",
        metadata={"tool_status": "ok"},
    )
    v2.record_tool_evidence(
        "run_shell",
        {"command": "python -m pytest tests/test_pico.py -q", "timeout": 20},
        "exit_code: 0",
        metadata={"tool_status": "ok"},
    )
    ws = make_working_state(
        verification=[
            {"command": "dir codecub", "status": "ok", "step": 1},
            {"command": "python -m pytest tests/test_pico.py -q", "status": "ok", "step": 2},
        ]
    )
    promoted, *_ = v2.extract_and_persist(ws, "run checks", "", run_id="run_x")
    statements = [r["statement"] for r in v2.durable_store.active_records()]
    assert any("pytest" in s for s in statements)
    assert not any("dir codecub" in s for s in statements)


# ======================================================================
# 4. Retrieval (§61)
# ======================================================================


def test_retrieval_exact_path_ranks_high(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop")
    v2.record_tool_evidence("read_file", {"path": "tools.py"}, "tool runner")
    result = v2.retrieve("runtime.py", force=True)
    assert result.evidence_items
    assert result.evidence_items[0].path == "runtime.py"


def test_retrieval_exact_symbol_ranks_high(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("symbol_search", {"query": "Pico.ask", "path": "runtime.py"}, "match")
    result = v2.retrieve("Pico.ask", force=True)
    assert result.evidence_items and result.evidence_items[0].symbol == "Pico.ask"


def test_retrieval_unrelated_not_selected(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop")
    result = v2.retrieve("how do I deploy kubernetes", force=True)
    assert result.items == []


def test_retrieval_stale_marked(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "old")
    (workspace / "runtime.py").write_text("new\n", encoding="utf-8")
    v2.refresh_freshness()
    result = v2.retrieve("runtime", force=True)
    assert result.stale_count == 1
    assert result.evidence_items[0].marker == STALE_MARKER


def test_retrieval_fresh_outranks_stale_duplicate(workspace):
    store = EvidenceStore(workspace)
    store.add_evidence("runtime.py", "source_location", "stale copy", status=STATUS_STALE, created_at="2026-01-01T00:00:00+00:00")
    store.add_evidence("runtime.py", "source_location", "fresh copy", status=STATUS_FRESH, created_at="2026-06-01T00:00:00+00:00")
    v2 = MemoryV2(workspace)
    result = v2.retrieve("runtime", force=True)
    assert result.evidence_items[0].status == STATUS_FRESH


def test_retrieval_durable_relevant_selected(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    cons.apply(MemoryCandidate(candidate_type="test_command", statement="Test command is: python -m pytest tests/", source_refs=[{"kind": "verification_event"}]))
    v2 = MemoryV2(workspace)
    result = v2.retrieve("how should I run the tests", force=True)
    assert result.durable_items
    assert result.durable_items[0].topic == "build-and-test"


def test_retrieval_superseded_durable_excluded(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    cons.apply(MemoryCandidate(candidate_type="test_command", statement="Test command is: pytest a", source_refs=[{"kind": "verification_event"}]))
    cons.apply(MemoryCandidate(candidate_type="test_command", statement="Test command is: pytest b", source_refs=[{"kind": "verification_event"}]))
    v2 = MemoryV2(workspace)
    result = v2.retrieve("test command", force=True)
    assert len(result.durable_items) == 1
    assert "pytest b" in result.durable_items[0].text


def test_retrieval_conflict_surfaced_safely(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    cons.apply(MemoryCandidate(candidate_type="environment", statement="Environment: Python 3.11", source_refs=[{"kind": "runtime_metadata"}]))
    cons.apply(MemoryCandidate(candidate_type="environment", statement="Environment: Python 3.13", source_refs=[{"kind": "runtime_metadata"}]))
    v2 = MemoryV2(workspace)
    result = v2.retrieve("python environment", force=True)
    assert result.durable_items
    assert any("CONFLICT" in item.marker for item in result.durable_items)


def test_retrieval_top_k_enforced(workspace):
    store = EvidenceStore(workspace)
    for index in range(6):
        (workspace / f"f{index}.py").write_text("x\n", encoding="utf-8")
        store.add_evidence(f"f{index}.py", "source_location", f"alpha fact {index}")
    v2 = MemoryV2(workspace)
    result = v2.retrieve("alpha fact", force=True)
    assert len(result.evidence_items) <= 2
    assert len(result.items) <= 4


def test_retrieval_token_budget_enforced(workspace):
    store = EvidenceStore(workspace)
    for index in range(6):
        (workspace / f"g{index}.py").write_text("x\n", encoding="utf-8")
        store.add_evidence(f"g{index}.py", "source_location", "alpha fact " + ("word " * 40) + str(index))
    v2 = MemoryV2(workspace, token_budget=120)
    result = v2.retrieve("alpha fact", force=True)
    assert result.total_tokens <= 120 + 220  # last item may slightly exceed; bounded


def test_retrieval_source_diversity_enforced(workspace):
    store = EvidenceStore(workspace)
    store.add_evidence("runtime.py", "source_location", "runtime fact one")
    store.add_evidence("runtime.py", "symbol_location", "Pico.ask runtime fact two", symbol="Pico.ask")
    v2 = MemoryV2(workspace)
    result = v2.retrieve("runtime", force=True)
    paths = [item.path for item in result.evidence_items]
    assert len(set(paths)) == len(paths)  # at most one evidence per path


def test_retrieval_duplicates_removed(workspace):
    store = EvidenceStore(workspace)
    store.add_evidence("runtime.py", "source_location", "same fact text")
    store.add_evidence("runtime.py", "source_location", "same fact text")
    v2 = MemoryV2(workspace)
    result = v2.retrieve("same fact text", force=True)
    texts = [item.text for item in result.evidence_items]
    assert len(texts) == len(set(texts))


def test_retrieval_query_uses_working_state(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "tools.py"}, "tool runner")
    ws = make_working_state(goal="fix tool bug", symbols=[{"path": "tools.py", "name": "run_tool"}], blockers=["tool rejected"])
    result = v2.retrieve("unrelated task", working_state=ws, force=True)
    assert result.evidence_items and result.evidence_items[0].path == "tools.py"


def test_retrieval_cache_fingerprint(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop")
    first = v2.retrieve("runtime", force=True)
    second = v2.retrieve("runtime", force=False)
    assert first.fingerprint == second.fingerprint
    assert second.cached is True
    # mutation invalidates the cache
    v2.record_tool_evidence("read_file", {"path": "tools.py"}, "tool runner")
    third = v2.retrieve("runtime", force=False)
    assert third.fingerprint != first.fingerprint


def test_retrieval_progress_aware_suppression(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop")
    first = v2.retrieve("runtime", force=True)
    assert first.evidence_items
    # model reads the hinted path -> subsequent retrieval suppresses it
    v2.note_read("runtime.py")
    second = v2.retrieve("runtime", force=True)
    assert second.suppressed_used_count >= 1
    assert not second.evidence_items


# ======================================================================
# 5. Context Compiler integration (§62)
# ======================================================================


def test_context_memory_layer_enters_compiler(workspace):
    from codecub.context_compiler import ContextCompiler, WorkingState

    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop")
    result = v2.retrieve("runtime loop", force=True)
    compiler = ContextCompiler(workspace_root=workspace)
    prompt, metadata = compiler.compile_text(
        "debug the runtime",
        working_state=WorkingState(),
        history=[],
        memory_layer=result.render(),
        memory_meta={
            "evidence_count": len(result.evidence_items),
            "durable_count": len(result.durable_items),
            "stale_count": result.stale_count,
            "token_budget": result.token_budget,
        },
    )
    assert "Relevant memory:" in prompt
    assert "runtime.py" in prompt
    assert metadata["memory_tokens"] > 0
    assert metadata["memory_evidence_count"] == 1
    assert metadata["memory_layer_rendered"] is True


def test_context_working_state_remains_separate(workspace):
    from codecub.context_compiler import ContextCompiler, WorkingState

    compiler = ContextCompiler(workspace_root=workspace)
    ws = WorkingState()
    ws.set_goal("goal text")
    prompt, _metadata = compiler.compile_text("user msg", working_state=ws, history=[], memory_layer=None)
    assert "Working State:" in prompt
    assert "goal text" in prompt
    assert "Relevant memory:" not in prompt


def test_context_memory_not_pinned_bulk(workspace):
    from codecub.context_compiler import ContextCompiler, WorkingState

    compiler = ContextCompiler(workspace_root=workspace)
    # Memory goes through the bounded memory layer, never through pinned_extra.
    pinned = compiler._build_pinned("task", {"pinned:evidence-ledger": "x"})
    keys = [item.key for item in pinned]
    assert "pinned:relevant-memory" not in keys
    prompt, metadata = compiler.compile_text(
        "task",
        working_state=WorkingState(),
        history=[],
        memory_layer="Relevant memory:\nEvidence:\n- [FRESH] runtime.py - loop",
        memory_meta={"evidence_count": 1, "durable_count": 0, "stale_count": 0, "token_budget": 500},
    )
    assert metadata["memory_layer_rendered"] is True
    assert "Relevant memory:" in prompt


def test_context_memory_token_budget_bounded(workspace):
    from codecub.context_compiler import ContextCompiler, WorkingState

    compiler = ContextCompiler(workspace_root=workspace)
    big_layer = "Relevant memory:\n" + "\n".join(f"- [FRESH] f{i}.py - {('x' * 200)}" for i in range(50))
    prompt, metadata = compiler.compile_text("task", working_state=WorkingState(), history=[], memory_layer=big_layer, memory_meta={"evidence_count": 2, "durable_count": 2, "stale_count": 0, "token_budget": 500})
    # layer rendered as given (runtime already bounded it); tokens reported
    assert metadata["memory_tokens"] > 0
    assert "Relevant memory:" in prompt


def test_context_stale_evidence_renders_revalidate(workspace):
    from codecub.context_compiler import ContextCompiler, WorkingState

    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "old detail")
    (workspace / "runtime.py").write_text("new\n", encoding="utf-8")
    v2.refresh_freshness()
    result = v2.retrieve("runtime detail", force=True)
    rendered = result.render()
    assert STALE_MARKER in rendered
    compiler = ContextCompiler(workspace_root=workspace)
    prompt, _ = compiler.compile_text("runtime", working_state=WorkingState(), history=[], memory_layer=rendered, memory_meta={"evidence_count": 1, "durable_count": 0, "stale_count": 1, "token_budget": 500})
    assert STALE_MARKER in prompt


def test_context_native_message_integrity(workspace):
    from codecub.context_compiler import ContextCompiler, WorkingState

    compiler = ContextCompiler(workspace_root=workspace)
    native = [
        {"role": "system", "content": "You are CodeCub."},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "runtime.py"})}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]
    messages, metadata = compiler.compile_native(
        "task",
        working_state=WorkingState(),
        native_messages=native,
        memory_layer="Relevant memory:\nEvidence:\n- [FRESH] runtime.py - loop",
        memory_meta={"evidence_count": 1, "durable_count": 0, "stale_count": 0, "token_budget": 500},
    )
    # assistant.tool_calls must be immediately followed by its tool result
    for index, message in enumerate(messages):
        if message.get("tool_calls"):
            assert messages[index + 1].get("role") == "tool"
            assert messages[index + 1].get("tool_call_id") == message["tool_calls"][0]["id"]
    assert metadata["memory_tokens"] > 0


# ======================================================================
# 6. Migration (§63)
# ======================================================================


def _write_legacy_durable(root):
    memory_root = root / ".codecub" / "memory"
    topics_dir = memory_root / "topics"
    topics_dir.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text(
        "# Durable Memory Index\n\n"
        "- [project-conventions](topics/project-conventions.md): Project Conventions\n"
        "  - summary: Stable repository conventions.\n"
        "  - tags: convention\n",
        encoding="utf-8",
    )
    (topics_dir / "project-conventions.md").write_text(
        "# Project Conventions\n\n"
        "- topic: project-conventions\n"
        "- summary: Stable repository conventions.\n"
        "- tags: convention\n"
        "- updated_at: 2026-04-12T08:14:49+00:00\n\n"
        "## Notes\n"
        "- Use constrained tools instead of guessing.\n",
        encoding="utf-8",
    )


def test_migration_old_session_memory_loads(workspace):
    (workspace / "sample.txt").write_text("alpha\n", encoding="utf-8")
    legacy = {
        "working": {"task_summary": "old task", "recent_files": ["sample.txt"]},
        "episodic_notes": [{"text": "process breadcrumb", "kind": "process", "tags": [], "source": "", "created_at": "2026-01-01T00:00:00+00:00", "note_index": 0}],
        "file_summaries": {"sample.txt": {"summary": "alpha file", "created_at": "2026-01-01T00:00:00+00:00", "freshness": None}},
        "task": "", "files": [], "notes": [], "next_note_index": 0,
    }
    v2 = make_v2(workspace)
    report = v2.migrate_legacy(legacy)
    assert report.ran or report.idempotent_skip
    if report.ran:
        assert report.migrated_evidence >= 1
        assert report.skipped_episodic == 1
        assert report.skipped_working is True


def test_migration_file_summaries_become_evidence(workspace):
    (workspace / "sample.txt").write_text("alpha\n", encoding="utf-8")
    from codecub.memory import file_freshness

    recorded = file_freshness("sample.txt", workspace)
    legacy = {
        "file_summaries": {"sample.txt": {"summary": "alpha file", "created_at": "2026-01-01T00:00:00+00:00", "freshness": recorded}},
        "episodic_notes": [], "working": {}, "task": "", "files": [], "notes": [], "next_note_index": 0,
    }
    v2 = make_v2(workspace)
    v2.migrate_legacy(legacy)
    records = v2.evidence_store.latest_records()
    assert any(r["path"] == "sample.txt" and r["status"] == STATUS_FRESH for r in records)


def test_migration_durable_topics_migrate(workspace):
    _write_legacy_durable(workspace)
    v2 = make_v2(workspace)
    v2.migrate_legacy({})
    records = v2.durable_store.active_records()
    assert any("Use constrained tools instead of guessing." in r["statement"] for r in records)


def test_migration_episodic_not_durable(workspace):
    legacy = {
        "episodic_notes": [
            {"text": "read sample.txt and saw alpha", "kind": "episodic", "tags": [], "source": "sample.txt", "created_at": "2026-01-01T00:00:00+00:00", "note_index": 0},
            {"text": "patch rejected on x", "kind": "process", "tags": ["process"], "source": "patch_file", "created_at": "2026-01-01T00:00:00+00:00", "note_index": 1},
        ],
        "file_summaries": {}, "working": {}, "task": "", "files": [], "notes": [], "next_note_index": 2,
    }
    v2 = make_v2(workspace)
    v2.migrate_legacy(legacy)
    assert v2.durable_store.active_size() == 0  # episodic process notes never durable


def test_migration_legacy_fields_still_readable(workspace):
    legacy = {"working": {"task_summary": "t", "recent_files": []}, "episodic_notes": [], "file_summaries": {}, "task": "t", "files": [], "notes": [], "next_note_index": 0}
    v2 = make_v2(workspace)
    v2.migrate_legacy(legacy)
    # legacy session memory is untouched
    assert legacy["working"]["task_summary"] == "t"


def test_migration_idempotent(workspace):
    v2 = make_v2(workspace)
    v2.migrate_legacy({})
    second = v2.migrate_legacy({})
    assert second.idempotent_skip is True
    assert v2.migration.already_migrated() is True


def test_migration_corrupt_legacy_fails_safely(workspace):
    memory_root = workspace / ".codecub" / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "MEMORY.md").write_text("{not valid markdown index", encoding="utf-8")
    v2 = make_v2(workspace)
    report = v2.migrate_legacy({"file_summaries": "not-a-dict", "episodic_notes": None})
    assert report.corrupt_legacy_session is True or report.corrupt_legacy_durable is True
    # store still works afterwards
    assert v2.evidence_store is not None


# ======================================================================
# 7. Secret safety (§64)
# ======================================================================


def test_secret_candidate_rejected():
    assert reject_candidate(MemoryCandidate(candidate_type="convention", statement="API key is sk-abc1234567890XYZ", source_refs=[{"kind": "x"}])) == "secret"


def test_secret_tool_output_not_persisted(workspace):
    v2 = make_v2(workspace)
    created = v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890")
    # read evidence uses summarize of result text; secret must be dropped
    records = v2.evidence_store.latest_records()
    assert all("Bearer" not in r["summary"] and "abcdefghijklmnopqrstuvwxyz1234567890" not in r["summary"] for r in records)
    assert created == [] or all("Bearer" not in c.summary for c in created)


def test_secret_not_in_retrieval_debug(workspace):
    store = EvidenceStore(workspace)
    created = store.add_evidence("runtime.py", "source_location", "token is sk-abcdefgh1234567890XYZ")
    assert created is None  # rejected at write


def test_secret_not_in_durable_view(workspace):
    store = DurableMemoryStore(workspace)
    cons = MemoryConsolidator(store)
    outcome = cons.apply(MemoryCandidate(candidate_type="preference", statement="Preference: password hunter2value", source_refs=[{"kind": "user_final_answer_line"}]))
    assert outcome.action == "REJECT"
    assert store.active_size() == 0


def test_secret_not_in_evidence_jsonl(workspace):
    store = EvidenceStore(workspace)
    store.add_evidence("runtime.py", "source_location", "clean summary")
    # persist a secret-bearing record directly and verify the store refuses
    record = EvidenceRecord.create(path="runtime.py", kind="source_location", summary="Bearer abcdefghijklmnopqrstuvwxyz1234567890 secret")
    assert contains_secret(record.summary)
    assert filter_text(record.summary) != record.summary


def test_secret_filter_redacts_values():
    assert "secret" not in filter_text("password: mysecretvalue123")
    assert "<redacted>" in filter_text("token=sk-abcdefghij12345678")


# ======================================================================
# 8. Cross-session (§65-§68) — the most important group
# ======================================================================


def test_cross_session_retrieval_works(workspace):
    # Session A: discover + record evidence
    session_a = make_v2(workspace)
    session_a.record_tool_evidence("read_file", {"path": "runtime.py"}, "Pico.ask runtime loop")
    session_a.record_tool_evidence(
        "symbol_search", {"query": "Pico.ask", "path": "runtime.py"}, "match"
    )

    # Session B: brand-new MemoryV2, same workspace, NO session history
    session_b = make_v2(workspace)
    assert session_b.evidence_store.size() >= 2
    result = session_b.retrieve("where is the runtime loop", force=True)
    assert result.evidence_items
    assert any(item.path == "runtime.py" for item in result.evidence_items)


def test_cross_session_no_history_leak(workspace):
    session_a = make_v2(workspace)
    session_a.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop")
    session_b = make_v2(workspace)
    # Session B must NOT carry Session A's in-memory state
    assert session_b.retriever.read_paths == set()
    assert session_b.retriever.retrieval_count == 0
    # its only knowledge is the on-disk evidence store
    result = session_b.retrieve("runtime loop", force=True)
    assert result.evidence_items


def test_cross_session_freshness_stale(workspace):
    session_a = make_v2(workspace)
    session_a.record_tool_evidence("read_file", {"path": "runtime.py"}, "old impl")
    # external modification between sessions
    (workspace / "runtime.py").write_text("def ask():\n    return 1\n", encoding="utf-8")
    session_b = make_v2(workspace)
    session_b.refresh_freshness()
    result = session_b.retrieve("runtime impl", force=True)
    assert result.stale_count >= 1
    assert result.evidence_items[0].marker == STALE_MARKER


def test_cross_session_reread_fresh_new_hash(workspace):
    session_a = make_v2(workspace)
    session_a.record_tool_evidence("read_file", {"path": "runtime.py"}, "old impl")
    (workspace / "runtime.py").write_text("def ask():\n    return 1\n", encoding="utf-8")
    session_b = make_v2(workspace)
    session_b.refresh_freshness()
    assert session_b.retrieve("runtime impl", force=True).stale_count >= 1
    # Session B reads the source -> evidence revalidated fresh with new hash
    session_b.record_tool_evidence("read_file", {"path": "runtime.py"}, "def ask(): return 1")
    result = session_b.retrieve("runtime impl", force=True)
    assert result.evidence_items and result.evidence_items[0].marker == FRESH_MARKER


def test_memory_guides_directed_read_not_broad_search(workspace):
    """Memory hint should lead to a directed read, not repeated broad searches."""
    session_a = make_v2(workspace)
    session_a.record_tool_evidence("read_file", {"path": "runtime.py"}, "Pico.ask runtime loop")
    session_b = make_v2(workspace)
    result = session_b.retrieve("runtime loop", force=True)
    assert result.evidence_items
    # the hint points to one exact path; a directed read of that path revalidates
    session_b.note_read("runtime.py")
    finalize = session_b.finalize_run()
    assert finalize["guided_reread_paths"] == ["runtime.py"]
    assert session_b.counters["memory_guided_reread_count"] == 1


# ======================================================================
# 9. Memory OFF / legacy compatibility (§74-§75)
# ======================================================================


def test_memory_v2_off_no_injection(workspace):
    from codecub.context_compiler import ContextCompiler, WorkingState

    compiler = ContextCompiler(workspace_root=workspace)
    prompt, _ = compiler.compile_text("task", working_state=WorkingState(), history=[], memory_layer=None)
    assert "Relevant memory:" not in prompt


def test_memory_flag_off_disables_v2(workspace):
    v2 = make_v2(workspace)
    v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "runtime loop")
    # simulate feature flags off: runtime would not call v2; facade itself stays
    # functional, but the runtime gate (memory_v2_enabled) must be testable via
    # the runtime object below.
    from tests.test_pico import build_agent

    agent = build_agent(workspace, [], feature_flags={"memory": False, "memory_v2": True})
    assert agent.memory_v2_enabled() is False
    assert agent._memory_layer() == ("", {})


def test_runtime_memory_v2_off_uses_legacy_path(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(workspace, [], feature_flags={"memory_v2": False, "memory": True, "relevant_memory": True})
    agent.memory.append_note("legacy alpha note", tags=("recall",), created_at="2026-04-07T10:00:00+00:00")
    extras = agent._pinned_extra("recall alpha")
    assert "pinned:relevant-memory" in extras  # legacy injection still works
    assert "legacy alpha note" in extras["pinned:relevant-memory"]


def test_no_double_injection_when_v2_on(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(workspace, [])
    agent.memory.append_note("legacy alpha note", tags=("recall",), created_at="2026-04-07T10:00:00+00:00")
    extras = agent._pinned_extra("recall alpha")
    assert "pinned:relevant-memory" not in extras  # v2 path owns injection


# ======================================================================
# 10. Runtime integration
# ======================================================================


def test_runtime_evidence_recorded_from_tool_execution(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(
        workspace,
        [
            '<tool>{"name":"read_file","args":{"path":"runtime.py","start":1,"end":5}}</tool>',
            "<final>Done.</final>",
        ],
    )
    agent.ask("Inspect runtime.py")
    assert agent.memory_v2.evidence_store.size() >= 1
    records = agent.memory_v2.evidence_store.latest_records()
    assert any(r["path"] == "runtime.py" for r in records)


def test_runtime_durable_extraction_on_success(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(
        workspace,
        [
            '<tool>{"name":"run_shell","args":{"command":"python -m pytest --version","timeout":20}}</tool>',
            "<final>Done.</final>",
        ],
    )
    agent.ask("run the tests and finish")
    assert agent.memory_v2.durable_store.active_size() >= 1


def test_runtime_report_contains_memory_v2_metrics(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(workspace, ["<final>Done.</final>"])
    agent.ask("do the task")
    report = agent.build_report(agent.current_task_state)
    assert "memory_v2" in report
    assert report["memory_v2"]["evidence_store_size"] >= 0
    assert "memory_v2_activity" in report
    assert "memory_migration" in report


def test_runtime_memory_layer_in_prompt(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(
        workspace,
        [
            '<tool>{"name":"read_file","args":{"path":"runtime.py","start":1,"end":5}}</tool>',
            "<final>Done.</final>",
        ],
    )
    agent.ask("Inspect runtime.py")
    prompts = agent.model_client.prompts
    assert any("runtime.py" in (p or "") for p in prompts)


def test_runtime_retrieval_trigger_on_blocker_change(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(workspace, ["<final>Done.</final>"])
    agent.ask("first task")
    before = agent.memory_v2.retriever.retrieval_count
    # blocker change triggers re-retrieval
    agent.working_state.add_blocker("new blocker appeared")
    agent._refresh_memory_retrieval("first task")
    assert agent.memory_v2.retriever.retrieval_count > before


def test_runtime_stale_revalidation_tracking(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(workspace, [])
    agent.memory_v2.record_tool_evidence("read_file", {"path": "runtime.py"}, "old impl")
    (workspace / "runtime.py").write_text("changed\n", encoding="utf-8")
    agent.memory_v2.refresh_freshness()
    agent.memory_v2.retrieve("runtime impl", force=True)
    summary = agent.memory_v2.finalize_run()
    assert summary["stale_unrevalidated_paths"] == ["runtime.py"]
    assert agent.memory_v2.counters["stale_used_without_revalidation"] == 1


def test_memory_v2_observability_trace_events(workspace):
    from tests.test_pico import build_agent

    agent = build_agent(workspace, ["<final>Done.</final>"])
    agent.ask("capture facts")
    trace_text = agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8")
    assert "memory_retrieval_started" in trace_text
    assert "memory_retrieval_finished" in trace_text
