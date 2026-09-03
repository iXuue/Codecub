"""Independent Phase 7B ContextValidator evidence and invariant tests."""

from types import SimpleNamespace

import pytest

from codecub.context_assembler import AssembledContext
from codecub.context_compiler import ContextItem, WorkingState
from codecub.context_validator import (
    ALLOW,
    INVALID,
    RETRY_ASSEMBLY,
    VALID,
    VALID_WITH_FALLBACK,
    ContextValidationResult,
    ContextValidator,
)


_MISSING = object()


def _context(
    *,
    protocol="legacy_text",
    prompt="task",
    messages=None,
    final_tokens=10,
    budget=20,
    requirements=None,
    state=_MISSING,
    segments=None,
    metadata=None,
):
    if state is _MISSING:
        state = WorkingState()
    compiled = SimpleNamespace(
        working_state=state,
        pinned=list(segments or []),
        recent_items=[],
        raw_evidence_items=[],
        compressed_history_items=[],
        repo_map_items=[],
    )
    context_metadata = {
        "compiled_context_tokens": final_tokens,
        "candidate_context_tokens": final_tokens,
        "usable_input_budget": budget,
    }
    context_metadata.update(metadata or {})
    return AssembledContext(
        protocol=protocol,
        prompt=prompt,
        messages=list(messages or []),
        metadata=context_metadata,
        compiled_context=compiled,
        segments=list(segments or []),
        validation_requirements=dict(requirements or {}),
    )


def _validator(**kwargs):
    return ContextValidator(**kwargs)


def test_valid_minimal_context_has_structured_budget_evidence():
    result = _validator().validate(_context())

    assert result.status == VALID_WITH_FALLBACK or result.status == VALID
    assert result.action == ALLOW
    assert result.valid
    assert result.budget_ok is True
    assert result.evidence.final_tokens == 10
    assert result.evidence.budget == 20


def test_valid_normal_context_preserves_required_working_state_and_protected_segment():
    state = WorkingState(goal="repair auth")
    protected = ContextItem(
        "pinned:runtime-constraints",
        "pinned",
        "Protected runtime constraints (must obey):\n- Do not modify auth.py",
        provenance={"protected": True},
    )
    result = _validator().validate(
        _context(
            prompt="Working State:\n- Goal: repair auth\nProtected runtime constraints (must obey):\n- Do not modify auth.py",
            requirements={
                "required_sources": ("working_state",),
                "working_state_required": True,
                "protected_constraints": ("Do not modify auth.py",),
            },
            state=state,
            segments=[protected],
        )
    )

    assert result.status in {VALID, VALID_WITH_FALLBACK}
    assert result.evidence.working_state_present is True
    assert result.evidence.protected_constraints_present is True
    assert result.evidence.hard_failures == ()


def test_budget_exactly_at_limit_is_valid_and_over_budget_is_retryable_invalid():
    validator = _validator()
    exact = validator.validate(_context(final_tokens=20, budget=20))
    over = validator.validate(_context(final_tokens=21, budget=20))

    assert exact.budget_ok is True
    assert exact.valid
    assert over.status == INVALID
    assert over.action == RETRY_ASSEMBLY
    assert over.budget_ok is False
    assert "BUDGET_EXCEEDED" in over.evidence.hard_failures


def test_character_budget_metadata_is_not_compared_with_token_budget():
    result = _validator().validate(
        _context(
            final_tokens=120,
            budget=20,
            metadata={
                "budget_mode": "char",
                "budget_unit": "chars",
                "prompt_budget_chars": 128,
            },
        )
    )

    assert result.budget_ok is True
    assert result.evidence.budget == 128
    assert result.valid


def test_provider_hard_limit_is_separate_from_compiler_budget():
    result = _validator().validate(
        _context(
            final_tokens=21,
            budget=30,
            requirements={"provider_context_limit": 20},
        )
    )

    assert result.budget_ok is True
    assert result.provider_limit_ok is False
    assert result.status == INVALID
    assert result.action == RETRY_ASSEMBLY


def test_protected_constraint_present_is_structural_and_missing_is_hard_invalid():
    present = ContextItem(
        "pinned:runtime-constraints",
        "pinned",
        "Protected runtime constraints (must obey):\n- Do not modify auth.py",
        provenance={"protected": True},
    )
    requirements = {"protected_constraints": ("Do not modify auth.py",)}
    valid = _validator().validate(
        _context(
            prompt="task",
            requirements=requirements,
            segments=[present],
        )
    )
    missing = _validator().validate(
        _context(prompt="task", requirements=requirements, segments=[])
    )

    assert valid.protected_constraints_present is True
    assert valid.valid
    assert missing.status == INVALID
    assert missing.action == "REJECT"
    assert "PROTECTED_CONSTRAINT_MISSING" in missing.evidence.hard_failures


def test_working_state_required_semantics_allow_optional_absence():
    required = _validator().validate(
        _context(
            prompt="task",
            requirements={"working_state_required": True},
            state=None,
        )
    )
    optional = _validator().validate(_context(prompt="task", state=None))

    assert required.status == INVALID
    assert required.evidence.working_state_present is False
    assert optional.valid
    assert optional.evidence.working_state_present is None


def test_fresh_repository_evidence_is_valid_and_stale_evidence_is_retryable():
    fresh = _validator(freshness_checker=lambda path: "hash-ok").validate(
        _context(
            requirements={
                "freshness_required": True,
                "freshness_entries": ({"path": "auth.py", "freshness": "hash-ok"},),
            }
        )
    )
    stale = _validator(freshness_checker=lambda path: "hash-new").validate(
        _context(
            requirements={
                "freshness_required": True,
                "freshness_entries": ({"path": "auth.py", "freshness": "hash-old"},),
            }
        )
    )

    assert fresh.freshness_ok is True
    assert fresh.valid
    assert stale.freshness_ok is False
    assert stale.status == INVALID
    assert stale.action == RETRY_ASSEMBLY
    assert "STALE_REQUIRED_CONTEXT" in stale.evidence.hard_failures


def test_declared_retrieval_evidence_requires_presence_but_empty_optional_retrieval_is_valid():
    valid = _validator().validate(
        _context(
            prompt="task [FRESH] evidence-1",
            requirements={
                "required_sources": ("retrieval",),
                "retrieval_evidence": ({"marker": "evidence-1"},),
            },
        )
    )
    missing = _validator().validate(
        _context(
            requirements={
                "required_sources": ("retrieval",),
                "retrieval_evidence": ({"marker": "evidence-1"},),
            }
        )
    )
    optional = _validator().validate(
        _context(requirements={"optional_sources": ("retrieval",)})
    )

    assert valid.retrieval_evidence_valid is True
    assert valid.valid
    assert missing.status == INVALID
    assert "RETRIEVAL_EVIDENCE_MISSING" in missing.evidence.hard_failures
    assert optional.valid


def _native_messages(tool_call_id="call-1"):
    return [
        {"role": "system", "content": "native seed"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": tool_call_id, "content": "ok"},
        {"role": "user", "content": "continue"},
    ]


def test_native_valid_tool_continuity_and_mismatched_tool_call_id():
    valid = _validator().validate(
        _context(
            protocol="native_tools",
            messages=_native_messages(),
        )
    )
    mismatched = _validator().validate(
        _context(
            protocol="native_tools",
            messages=[
                *_native_messages()[:3],
                {"role": "tool", "tool_call_id": "unknown", "content": "bad"},
            ],
        )
    )

    assert valid.native_continuity_ok is True
    assert valid.valid
    assert mismatched.status == INVALID
    assert mismatched.action == "REJECT"
    assert "NATIVE_CONTINUITY_INVALID" in mismatched.evidence.hard_failures


@pytest.mark.parametrize("protocol", ["legacy_text", "legacy_stream"])
def test_legacy_text_and_streaming_inputs_are_supported(protocol):
    result = _validator().validate(_context(protocol=protocol), protocol=protocol)

    assert result.valid
    assert result.evidence.checks[-1].name == "legacy_input"


def test_fallback_evidence_is_explicit_and_validation_is_deterministic():
    context = _context(metadata={"budget_source": "fallback"})
    validator = _validator()
    first = validator.validate(context).to_dict()
    second = validator.validate(context).to_dict()

    assert first == second
    assert first["status"] == VALID_WITH_FALLBACK
    assert first["evidence"]["fallback_used"] is True
    assert first["evidence"]["fallback_reason"] == "BUDGET_FALLBACK"


def test_validation_result_can_be_consumed_as_structured_mapping():
    result = _validator().validate(_context())

    assert isinstance(result, ContextValidationResult)
    assert result["evidence"]["budget_ok"] is True
    assert result["status"] in {VALID, VALID_WITH_FALLBACK}
