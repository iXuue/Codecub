"""Evaluation contracts used by Evolver V2.

This package deliberately owns dataset partition access, not benchmark
implementation.  It keeps development, holdout and regression evidence
separate so callers cannot accidentally pass holdout material into the
optimizer.
"""

from .registry import (
    EvaluationRegistry,
    EvaluationSplit,
    EvaluationTask,
    HoldoutLeakError,
    RegistryAccessError,
    SplitView,
)

__all__ = [
    "EvaluationRegistry",
    "EvaluationSplit",
    "EvaluationTask",
    "HoldoutLeakError",
    "RegistryAccessError",
    "SplitView",
    "RegressionResult",
    "run_regression",
]


def __getattr__(name):
    if name in {"RegressionResult", "run_regression"}:
        from . import regression

        return getattr(regression, name)
    raise AttributeError(name)
