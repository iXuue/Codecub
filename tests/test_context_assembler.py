"""Phase 7A — ContextAssembler ownership and production integration tests."""

from codecub.context_assembler import ContextAssembler, ContextSources
from codecub.context_compiler import ContextBudget, ContextCompiler, WorkingState
from tests.test_pico import build_agent


def _compiler():
    return ContextCompiler(
        budget=ContextBudget.resolve(context_window=32000, max_new_tokens=512)
    )


def test_assembler_is_independent_of_pico_and_preserves_existing_sources():
    state = WorkingState()
    state.set_goal("repair auth")
    state.add_known_fact("auth.py currently uses the legacy verifier")
    sources = ContextSources(
        user_message="repair auth",
        protected_constraints=("Do not modify auth.py",),
        working_state=state,
        history=({"role": "user", "content": "inspect auth.py"},),
        pinned_extra={"pinned:project-rules": "keep the existing verifier"},
        memory_layer="Memory layer: previous verifier failure",
        memory_meta={"evidence_count": 1},
    )

    result = ContextAssembler(_compiler()).assemble(sources)

    assert result.protocol == "legacy_text"
    assert result.model_input == result.prompt
    assert result.metadata["context_assembler"] == "context_assembler"
    assert "repair auth" in result.prompt
    assert result.prompt.count("Do not modify auth.py") == 1
    assert "keep the existing verifier" in result.prompt
    assert "auth.py currently uses the legacy verifier" in result.prompt
    assert "Memory layer: previous verifier failure" in result.prompt
    assert "inspect auth.py" in result.prompt


def test_assembler_order_is_deterministic_for_the_same_sources():
    sources = ContextSources(
        user_message="inspect files",
        working_state=WorkingState(),
        history=(
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ),
        pinned_extra={"pinned:safety": "safety", "pinned:rules": "rules"},
        memory_layer="memory",
    )

    first = ContextAssembler(_compiler()).assemble(sources)
    second = ContextAssembler(_compiler()).assemble(sources)

    assert first.prompt == second.prompt
    assert first.metadata["assembly_source_order"] == second.metadata[
        "assembly_source_order"
    ]


def test_assembler_keeps_native_protocol_messages_structured_and_contiguous():
    assembler = ContextAssembler(_compiler())
    native_history = assembler.initial_native_messages("inspect files")
    native_history.extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]
    )

    result = assembler.assemble(
        ContextSources(
            user_message="inspect files",
            protocol="native_tools",
            native_messages=tuple(native_history),
            working_state=WorkingState(),
        )
    )

    assert result.protocol == "native_tools"
    assert isinstance(result.model_input, list)
    assistant_index = next(
        index
        for index, message in enumerate(result.messages)
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert result.messages[assistant_index + 1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "ok",
    }
    assert result.metadata["assembly_protocol"] == "native_tools"


def test_production_ask_uses_assembler_for_each_legacy_model_context(tmp_path):
    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
            "<final>done</final>",
        ],
    )
    assembler = agent.context_assembler
    assembled = []
    original_assemble = assembler.assemble

    def recording_assemble(*args, **kwargs):
        result = original_assemble(*args, **kwargs)
        assembled.append(result)
        return result

    assembler.assemble = recording_assemble

    assert agent.ask("read README", run_id="context-assembler-spy") == "done"
    assert len(assembled) >= 2
    assert all(item.metadata["context_assembler"] == "context_assembler" for item in assembled)
    assert assembled[0].protocol == "legacy_text"
    assert "ok" in assembled[-1].prompt
    assert assembler.assemble_call_count == len(assembled)


def test_assembler_native_input_can_carry_a_protected_constraint():
    assembler = ContextAssembler(_compiler())
    result = assembler.assemble(
        user_message="repair auth.py",
        protocol="native_tools",
        protected_constraints=("Do not modify auth.py",),
        native_messages=tuple(assembler.initial_native_messages("repair auth.py")),
        working_state=WorkingState(),
    )

    system_messages = [item for item in result.messages if item.get("role") == "system"]
    assert any("Do not modify auth.py" in item.get("content", "") for item in system_messages)
    assert result.metadata["context_assembler"] == "context_assembler"
