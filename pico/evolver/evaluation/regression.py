"""Post-candidate regression evaluation over an explicit registry split."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pico.evolver.evaluation.registry import EvaluationRegistry, EvaluationSplit
from pico.evolver.orchestrator.scoring import EvalFn, MeasurementStatus, TaskEval, measurement_validity


@dataclass(frozen=True)
class RegressionResult:
    configured: bool
    passed: bool
    task_ids: tuple[str, ...]
    candidate_evals: dict[str, TaskEval]
    baseline_evals: dict[str, TaskEval]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "passed": self.passed,
            "task_ids": list(self.task_ids),
            "candidate_evals": {key: value.to_dict() for key, value in self.candidate_evals.items()},
            "baseline_evals": {key: value.to_dict() for key, value in self.baseline_evals.items()},
            "reason": self.reason,
        }


def run_regression(
    eval_fn: EvalFn,
    candidate: Any,
    baseline: Any,
    *,
    registry: EvaluationRegistry,
    k: int,
    job_name: str,
    expected_attempts: int | None = None,
) -> RegressionResult:
    """Evaluate candidate and baseline on registry-owned regression tasks.

    An empty regression split is reported as unconfigured rather than passing
    silently.  Any missing/infra/incomplete result fails the regression check.
    """

    task_ids = tuple(registry.task_ids(EvaluationSplit.REGRESSION, consumer="regression_evaluator"))
    if not task_ids:
        return RegressionResult(False, False, (), {}, {}, "regression split is not configured")
    expected = k if expected_attempts is None else expected_attempts
    candidate_evals = dict(eval_fn(candidate, list(task_ids), k, f"{job_name}_candidate", split="regression"))
    baseline_evals = dict(eval_fn(baseline, list(task_ids), k, f"{job_name}_baseline", split="regression"))
    candidate_validity = measurement_validity(candidate_evals, list(task_ids), expected_attempts=expected)
    baseline_validity = measurement_validity(baseline_evals, list(task_ids), expected_attempts=expected)
    if candidate_validity.status is not MeasurementStatus.measured:
        return RegressionResult(
            True,
            False,
            task_ids,
            candidate_evals,
            baseline_evals,
            f"candidate regression measurement invalid: {candidate_validity.to_dict()}",
        )
    if baseline_validity.status is not MeasurementStatus.measured:
        return RegressionResult(
            True,
            False,
            task_ids,
            candidate_evals,
            baseline_evals,
            f"baseline regression measurement invalid: {baseline_validity.to_dict()}",
        )
    regressions = [
        task_id
        for task_id in task_ids
        if candidate_evals[task_id].pass_rate < baseline_evals[task_id].pass_rate
    ]
    return RegressionResult(
        True,
        not regressions,
        task_ids,
        candidate_evals,
        baseline_evals,
        "no regression against baseline" if not regressions else f"regressed tasks: {regressions}",
    )


__all__ = ["RegressionResult", "run_regression"]
