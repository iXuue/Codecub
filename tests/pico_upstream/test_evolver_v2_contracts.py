from __future__ import annotations

import pytest

from pico.evolver.analysis.failure_miner import (
    PRESERVED_TURN_TERMINAL_KINDS,
    FailureMiner,
)
from pico.evolver.candidate_pipeline import CandidateAdmissionError, CandidateBuilder
from pico.evolver.evaluation import EvaluationRegistry, EvaluationSplit, HoldoutLeakError, run_regression
from pico.evolver.orchestrator.scoring import TaskEval
from pico.evolver.orchestrator.termination import (
    CampaignBudget,
    CampaignBudgetExceeded,
    CampaignBudgetTracker,
)
from pico.evolver.promotion import GateEvidence, PromotionGate, PromotionLedger, PromotionStatus
from pico.evolver.proposal import (
    EvolutionProposal,
    EvolutionProposalValidator,
    EvolutionTargetLayer,
    ProposalAction,
    ProposalGenerator,
    default_target_registry,
)


def _proposal(layer: str, action: str = "MODIFY_EXISTING", target_id: str = "appworld.prompt"):
    return EvolutionProposal(
        target_layer=layer,
        action=action,
        target_id=target_id,
        root_cause="the agent does not verify state before finalizing",
        evidence=[{"trace_id": "trace-1", "signal": "verification_failure"}],
        reason="the same behavior occurs across independent development traces",
    )


def test_registry_is_disjoint_and_optimizer_can_only_see_development() -> None:
    registry = EvaluationRegistry(
        development_task_ids=["dev-1", "dev-2"],
        holdout_task_ids=["holdout-1"],
        regression_task_ids=["reg-1"],
    )

    assert registry.task_ids("train") == ["dev-1", "dev-2"]
    assert registry.task_ids(EvaluationSplit.HOLDOUT, consumer="sealed_evaluator") == ["holdout-1"]
    assert registry.task_ids("regression", consumer="regression_evaluator") == ["reg-1"]
    with pytest.raises(HoldoutLeakError):
        registry.task_ids("holdout")
    with pytest.raises(HoldoutLeakError):
        registry.validate_task_ids("development", ["holdout-1"])


def test_registry_rejects_overlapping_partitions() -> None:
    with pytest.raises(ValueError, match="appears in both"):
        EvaluationRegistry(development_task_ids=["same"], holdout_task_ids=["same"])


def test_failure_miner_derives_signature_without_new_terminal_states() -> None:
    registry = EvaluationRegistry(development_task_ids=["dev-1", "dev-2"], holdout_task_ids=["holdout-1"])
    records = [
        {
            "trace_id": "trace-1",
            "task_id": "dev-1",
            "kind": "limited",
            "stop_reason": "step_limit_reached",
            "events": [{"name": "tool.call", "attributes": {"tool.name": "execute"}}],
        },
        {
            "trace_id": "trace-2",
            "task_id": "dev-2",
            "kind": "limited",
            "stop_reason": "step_limit_reached",
            "events": [{"name": "tool.call", "attributes": {"tool.name": "execute"}}],
        },
    ]

    report = FailureMiner(registry).mine(records)

    assert len(report.findings) == 2
    assert report.findings[0].behavior_signature == report.findings[1].behavior_signature
    assert report.findings[0].failure_reason == "no_convergence_before_budget"
    assert set(report.terminal_counts) <= PRESERVED_TURN_TERMINAL_KINDS
    with pytest.raises(HoldoutLeakError):
        FailureMiner(registry).mine(
            [{"trace_id": "holdout-trace", "task_id": "holdout-1", "kind": "limited"}]
        )


def test_proposal_validator_supports_three_layers_and_fail_closes_workflow() -> None:
    validator = EvolutionProposalValidator(default_target_registry())

    assert validator.validate(_proposal("Prompt")).accepted
    assert validator.validate(_proposal("Tool / Policy", target_id="appworld.tool_policy")).accepted
    assert validator.validate(_proposal("Skill", target_id="builtin.weather")).accepted

    workflow = validator.validate(_proposal("WORKFLOW", target_id="future.workflow"))
    assert not workflow.accepted
    assert workflow.code == "WORKFLOW_NOT_IMPLEMENTED"
    assert "Agent Workflow Runtime" in workflow.reason

    unknown = validator.validate(_proposal("Prompt", target_id="arbitrary.file"))
    assert not unknown.accepted
    assert unknown.code == "UNKNOWN_TARGET"


def test_new_skill_requires_repeated_pattern_and_uses_only_safe_id() -> None:
    validator = EvolutionProposalValidator(default_target_registry())
    proposal = _proposal("SKILL", ProposalAction.CREATE_NEW, "verification-loop")
    rejected = validator.validate(proposal)
    assert rejected.code == "SKILL_PATTERN_NOT_STABLE"

    stable = EvolutionProposal(
        **{**proposal.__dict__, "repeated_pattern_count": 3}
    )
    accepted = validator.validate(stable)
    assert accepted.accepted
    assert accepted.target_paths == ("pico/memory_engine/skills/verification-loop/SKILL.md",)

    unsafe = EvolutionProposal(
        **{**proposal.__dict__, "target_id": "../escape", "repeated_pattern_count": 3}
    )
    assert validator.validate(unsafe).code == "UNKNOWN_TARGET"


def test_proposal_generator_consumes_development_findings_and_prefers_existing_targets() -> None:
    registry = EvaluationRegistry(development_task_ids=["dev-1"], holdout_task_ids=["holdout-1"])
    report = FailureMiner(registry).mine(
        [
            {
                "trace_id": "trace-1",
                "task_id": "dev-1",
                "kind": "model_error",
                "stop_reason": "tool_timeout",
                "events": [{"message": "tool execute timeout"}],
            }
        ]
    )
    proposals = ProposalGenerator(evaluation_registry=registry).generate(report)
    assert len(proposals) == 1
    assert proposals[0].target_layer is EvolutionTargetLayer.TOOL_POLICY
    assert proposals[0].action is ProposalAction.MODIFY_EXISTING
    with pytest.raises(HoldoutLeakError):
        ProposalGenerator(evaluation_registry=registry).generate(report, split="holdout")


def test_candidate_builder_requires_exact_target_paths_and_does_not_write_production() -> None:
    validator = EvolutionProposalValidator(default_target_registry())
    builder = CandidateBuilder(validator)
    proposal = _proposal("Prompt")

    candidate = builder.build(
        candidate_id="candidate-1",
        parent_id="C0",
        parent_sha="parent-sha",
        proposal=proposal,
        files={"benchmarks/appworld/agent_cli.py": b"APPWORLD_PROMPT = 'changed'\n"},
    )
    assert candidate.target_paths == ("benchmarks/appworld/agent_cli.py",)
    assert candidate.committed_sha is None

    with pytest.raises(CandidateAdmissionError, match="TARGET_PATH_MISMATCH"):
        builder.build(
            candidate_id="candidate-2",
            parent_id="C0",
            parent_sha="parent-sha",
            proposal=proposal,
            files={"pico/templates/AGENTS.md": b"outside\n"},
        )


def test_promotion_requires_human_finalize_and_persists_lineage(tmp_path) -> None:
    proposal = _proposal("Prompt")
    evidence = GateEvidence(
        development_passed=True,
        holdout_passed=True,
        regression_passed=True,
    )
    decision = PromotionGate().evaluate("candidate-1", evidence)
    assert decision.status is PromotionStatus.PROMOTABLE
    assert decision.active is False

    ledger = PromotionLedger(tmp_path / "promotion.json")
    record = ledger.record_gate(
        parent_id="C0",
        parent_sha="parent-sha",
        candidate_sha="candidate-sha",
        proposal=proposal,
        manifest_digest="manifest-sha",
        decision=decision,
    )
    assert record.decision.status is PromotionStatus.PROMOTABLE
    active = ledger.finalize("candidate-1", actor="reviewer@example", approved=True)
    assert active.decision.status is PromotionStatus.ACTIVE
    assert active.human_actor == "reviewer@example"
    restored = PromotionLedger(tmp_path / "promotion.json").get("candidate-1")
    assert restored is not None
    assert restored.decision.status is PromotionStatus.ACTIVE
    assert restored.proposal["target_layer"] == "PROMPT"

    rejected_decision = PromotionGate().evaluate("candidate-2", evidence)
    ledger.record_gate(
        parent_id="C0",
        parent_sha="parent-sha",
        candidate_sha="candidate-sha-2",
        proposal=proposal,
        manifest_digest="manifest-sha-2",
        decision=rejected_decision,
    )
    rejected = ledger.finalize("candidate-2", actor="human", approved=False)
    assert rejected.decision.status is PromotionStatus.REJECTED
    with pytest.raises(ValueError, match="only PROMOTABLE"):
        ledger.finalize("candidate-2", actor="human", approved=True)


def test_promotion_gate_rejects_holdout_leak() -> None:
    decision = PromotionGate().evaluate(
        "candidate-1",
        GateEvidence(
            development_passed=True,
            holdout_passed=True,
            regression_passed=True,
            holdout_leak=True,
        ),
    )
    assert decision.status is PromotionStatus.REJECTED
    assert "leaked" in decision.reason


def test_campaign_budget_is_hard_and_independent_of_round_patience() -> None:
    now = [0.0]
    tracker = CampaignBudgetTracker(
        CampaignBudget(max_candidates=2, max_evaluations=3, max_llm_calls=2, max_driver_tokens=10),
        clock=lambda: now[0],
    )
    tracker.consume(candidates=1, evaluations=2, llm_calls=1, driver_tokens=5)
    assert tracker.usage.candidates == 1
    with pytest.raises(CampaignBudgetExceeded, match="max_candidates"):
        tracker.consume(candidates=1, evaluations=1, llm_calls=1, driver_tokens=5)
    assert tracker.usage.candidates == 1
    now[0] = 0.0
    timed = CampaignBudgetTracker(CampaignBudget(max_wall_time_seconds=0.5), clock=lambda: now[0])
    now[0] = 1.0
    stopped, reason = timed.check()
    assert stopped is True
    assert "max_wall_time_seconds" in reason


def test_regression_runner_is_explicit_and_fails_on_a_real_regression() -> None:
    registry = EvaluationRegistry(
        development_task_ids=["dev-1"],
        holdout_task_ids=["holdout-1"],
        regression_task_ids=["reg-1"],
    )

    def eval_fn(node, task_ids, k, job_name, *, split):
        assert split == "regression"
        passed = 1 if node == "baseline" or job_name.endswith("baseline") else 0
        return {task_id: TaskEval(task_id, passed, 1) for task_id in task_ids}

    result = run_regression(
        eval_fn,
        "candidate",
        "baseline",
        registry=registry,
        k=1,
        job_name="candidate-1",
    )
    assert result.configured is True
    assert result.passed is False
    assert "regressed tasks" in result.reason
