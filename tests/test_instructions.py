"""Phase 7C independent InstructionResolver contract tests."""

import ast
from pathlib import Path

from codecub.instructions import (
    Instruction,
    InstructionLayer,
    InstructionResolver,
    InstructionScope,
)
from codecub.context_assembler import ContextAssembler, ContextSources
from codecub.context_compiler import ContextBudget, ContextCompiler, WorkingState


def test_empty_resolution_is_deterministic():
    result = InstructionResolver().resolve()

    assert result.instructions == ()
    assert result.deduplicated_count == 0
    assert result.conflicts == ()


def test_all_required_layers_are_normalized_without_rendering_metadata():
    result = InstructionResolver().resolve(
        [
            Instruction("runtime", source="runtime", protected=True),
            Instruction("protected", source="protected"),
            Instruction("repository", source="repo"),
            Instruction("agent", source="agent"),
            Instruction("user", source="user_instruction"),
            Instruction("task", source="task"),
        ]
    )

    assert [item.layer for item in result.instructions] == [
        InstructionLayer.RUNTIME_SAFETY,
        InstructionLayer.PROTECTED_RUNTIME,
        InstructionLayer.REPOSITORY,
        InstructionLayer.AGENT,
        InstructionLayer.USER,
        InstructionLayer.TASK,
    ]
    assert "source=" not in result.render()
    assert "layer=" not in result.render()


def test_non_conflicting_instructions_survive_and_order_is_reproducible():
    values = [
        Instruction("task B", source="task"),
        Instruction("repo A", source="repository"),
        Instruction("agent C", source="agent"),
    ]
    first = InstructionResolver().resolve(values)
    second = InstructionResolver().resolve(reversed(values))

    assert [item.content for item in first.instructions] == [
        item.content for item in second.instructions
    ]
    assert [item.content for item in first.instructions] == [
        "repo A",
        "agent C",
        "task B",
    ]


def test_exact_duplicates_are_removed_but_distinct_scopes_are_not():
    duplicate = Instruction("same", source="user", scope="global")
    result = InstructionResolver().resolve(
        [duplicate, duplicate, Instruction("same", source="user", scope="turn")]
    )

    assert len(result.instructions) == 2
    assert result.deduplicated_count == 1


def test_explicit_conflict_uses_layer_precedence_and_keeps_shadow_evidence():
    result = InstructionResolver().resolve(
        [
            Instruction(
                "May modify auth.py",
                source="user",
                layer="user",
                conflict_key="edit:auth.py",
                polarity="allow",
            ),
            Instruction(
                "Do not modify auth.py",
                source="protected_runtime",
                layer="protected_runtime",
                protected=True,
                conflict_key="edit:auth.py",
                polarity="deny",
            ),
        ]
    )

    assert [item.content for item in result.instructions] == ["Do not modify auth.py"]
    assert [item.content for item in result.shadowed] == ["May modify auth.py"]
    assert result.conflicts[0].winner_id == result.instructions[0].id
    assert result.conflicts[0].loser_id == result.shadowed[0].id


def test_obvious_allow_deny_prose_gets_a_conservative_structural_key():
    result = InstructionResolver().resolve(
        [
            Instruction("May modify auth.py", source="user"),
            Instruction("Never modify auth.py", source="repository"),
        ]
    )

    assert len(result.conflicts) == 1
    assert result.instructions[0].content == "Never modify auth.py"


def test_scope_filtering_prevents_role_and_repository_leakage():
    values = [
        Instruction("all", scope="global"),
        Instruction("repo A", scope="repository", scope_id="repo-a"),
        Instruction("research", scope="agent-role", scope_id="research"),
        Instruction("implement", scope="agent-role", scope_id="implement"),
        Instruction("tool", scope="tool", scope_id="read_file"),
        Instruction("turn", scope="turn", scope_id="turn-1"),
        Instruction("run", scope="run", scope_id="run-1"),
    ]
    result = InstructionResolver().resolve(
        values,
        agent_role="research",
        repository_id="repo-a",
        tool_name="read_file",
        turn_id="turn-1",
        run_id="run-1",
    )

    assert {item.content for item in result.instructions} == {
        "all",
        "repo A",
        "research",
        "tool",
        "turn",
        "run",
    }
    assert "implement" not in {item.content for item in result.instructions}


def test_scope_id_without_an_active_scope_is_not_leaked():
    result = InstructionResolver().resolve(
        [Instruction("research only", scope=InstructionScope.AGENT_ROLE, scope_id="research")]
    )

    assert result.instructions == ()


def test_stable_ids_do_not_depend_on_input_order():
    item = Instruction("same", source="repository", scope="repository", scope_id="repo")

    first = InstructionResolver().resolve([item], repository_id="repo").instructions[0].id
    second = InstructionResolver().resolve(
        [Instruction(**item.to_dict())], repository_id="repo"
    ).instructions[0].id

    assert first == second


def test_resolver_has_no_runtime_or_pico_dependency():
    source = Path(__file__).parents[1] / "codecub" / "instructions.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )
    assert not any(name.endswith("runtime") or name.endswith("pico") for name in imported)


def test_assembler_consumes_resolved_instruction_segments_without_metadata_leak():
    resolved = InstructionResolver().resolve(
        [
            Instruction(
                "Keep changes minimal",
                source="user",
                layer="user",
                scope="turn",
                scope_id="turn-1",
            )
        ],
        turn_id="turn-1",
    )
    assembler = ContextAssembler(
        ContextCompiler(
            budget=ContextBudget.resolve(context_window=32000, max_new_tokens=512)
        )
    )

    result = assembler.assemble(
        ContextSources(
            user_message="inspect",
            working_state=WorkingState(),
            resolved_instructions=resolved,
        )
    )

    instruction_segments = [
        item for item in result.segments if item.kind == "instruction"
    ]
    assert any(item.text == "Keep changes minimal" for item in instruction_segments)
    assert instruction_segments[0].provenance["source"] == "user"
    assert "priority=" not in result.prompt
    assert "source=" not in result.prompt
