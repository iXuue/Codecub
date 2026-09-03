"""Independent ContextAssembler contract coverage."""

from codecub.context_assembler import ContextAssembler, ContextSources
from codecub.context_compiler import ContextBudget, ContextCompiler, WorkingState


def _assembler():
    return ContextAssembler(
        ContextCompiler(
            budget=ContextBudget.resolve(context_window=32000, max_new_tokens=512)
        )
    )


def test_only_user_input_and_empty_optional_sources_are_supported():
    result = _assembler().assemble(ContextSources(user_message="only user input"))

    assert result.prompt.startswith("User task: only user input")
    assert "Transcript:" not in result.prompt
    assert result.metadata["compiled_history_tokens"] == 0


def test_history_working_state_and_memory_are_combined_once():
    state = WorkingState()
    state.set_goal("goal")
    state.add_known_fact("working fact")
    result = _assembler().assemble(
        ContextSources(
            user_message="current request",
            working_state=state,
            history=({"role": "tool", "name": "read_file", "content": "tool result"},),
            memory_layer="retrieval evidence and memory",
        )
    )

    assert result.prompt.count("working fact") == 1
    assert result.prompt.count("tool result") == 1
    assert result.prompt.count("retrieval evidence and memory") == 1


def test_duplicate_pinned_sources_follow_explicit_input_order_without_hidden_copies():
    result = _assembler().assemble(
        ContextSources(
            user_message="task",
            pinned_extra={"pinned:first": "duplicate", "pinned:second": "duplicate"},
        )
    )

    assert result.prompt.count("duplicate") == 2


def test_token_freshness_and_range_metadata_survive_assembly():
    state = WorkingState()
    state.add_known_fact(
        "fresh fact",
        provenance={"path": "src/auth.py"},
        source_hash="known-hash",
    )
    state.add_read_range("src/auth.py", 1, 12, step=3, freshness="unchanged")
    result = _assembler().assemble(
        ContextSources(
            user_message="inspect auth",
            working_state=state,
            memory_layer="bounded memory",
            memory_meta={"evidence_count": 2, "token_budget": 100},
        )
    )

    assert result.metadata["compiled_context_tokens"] > 0
    assert result.metadata["memory_tokens"] > 0
    assert result.metadata["fresh_fact_count"] == 1
    assert "src/auth.py L1-L12 @ step 3, unchanged" in result.prompt


def test_assembler_accepts_a_narrow_compiler_without_runtime_dependencies():
    calls = []

    class Compiler:
        def compile_text(self, user_message, **kwargs):
            calls.append((user_message, kwargs))
            return "compiled user", {"compiled_context_tokens": 2}

    result = ContextAssembler(Compiler()).assemble(
        user_message="user",
        protected_constraints=("constraint",),
        history=[{"role": "user", "content": "history"}],
    )

    assert result.prompt == "compiled user"
    assert calls[0][0].endswith("- constraint")
    assert calls[0][1]["history"] == [{"role": "user", "content": "history"}]
