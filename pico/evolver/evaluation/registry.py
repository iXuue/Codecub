"""隔离 Evolver V2 的 development / holdout / regression 评测分区。

The registry is intentionally small and benchmark-neutral.  It does not load
task bodies; it only owns task identity and access policy.  A benchmark adapter
may use :meth:`task_ids` to select work and may resolve task bodies after the
caller has been authorized for the corresponding split.

The optimizer is allowed to see development identities and redacted failure
summaries only.  Holdout identities, task bodies and measurements are reserved
for the sealed evaluator.  Regression tasks are evaluator-only as well because
they are part of the post-candidate safety check, not optimization evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping


class RegistryAccessError(ValueError):
    """A caller requested a split or task it is not authorized to read."""


class HoldoutLeakError(RegistryAccessError):
    """Raised when holdout material is requested from an optimization context."""


class EvaluationSplit(StrEnum):
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"
    REGRESSION = "regression"

    @classmethod
    def parse(cls, value: "EvaluationSplit | str") -> "EvaluationSplit":
        if isinstance(value, cls):
            return value
        raw = str(value).strip().lower().replace("-", "_")
        aliases = {
            "dev": cls.DEVELOPMENT,
            "train": cls.DEVELOPMENT,
            "development": cls.DEVELOPMENT,
            "holdout": cls.HOLDOUT,
            "test": cls.HOLDOUT,
            "regression": cls.REGRESSION,
            "regress": cls.REGRESSION,
        }
        try:
            return aliases[raw]
        except KeyError as exc:
            raise RegistryAccessError(f"unknown evaluation split: {value!r}") from exc


class _Consumer(StrEnum):
    OPTIMIZER = "optimizer"
    SEALED_EVALUATOR = "sealed_evaluator"
    REGRESSION_EVALUATOR = "regression_evaluator"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True)
class EvaluationTask:
    """Task identity plus non-sensitive metadata held by the registry."""

    task_id: str
    split: EvaluationSplit
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        task_id = str(self.task_id).strip()
        if not task_id:
            raise ValueError("EvaluationTask.task_id must not be empty")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "split", EvaluationSplit.parse(self.split))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


@dataclass(frozen=True)
class SplitView:
    """An authorized, body-free view passed into orchestration code."""

    split: EvaluationSplit
    task_ids: tuple[str, ...]
    consumer: str
    redacted: bool = True

    def __post_init__(self) -> None:
        if not self.redacted and self.split is EvaluationSplit.HOLDOUT:
            raise ValueError("holdout SplitView must remain redacted")


def _normalise_ids(values: Iterable[str], *, field: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        task_id = str(value).strip()
        if not task_id:
            raise ValueError(f"{field} contains an empty task id")
        if task_id not in result:
            result.append(task_id)
    return tuple(result)


class EvaluationRegistry:
    """Owns disjoint task partitions and enforces consumer access policy."""

    def __init__(
        self,
        *,
        development_task_ids: Iterable[str],
        holdout_task_ids: Iterable[str],
        regression_task_ids: Iterable[str] = (),
        task_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        partitions = {
            EvaluationSplit.DEVELOPMENT: _normalise_ids(development_task_ids, field="development_task_ids"),
            EvaluationSplit.HOLDOUT: _normalise_ids(holdout_task_ids, field="holdout_task_ids"),
            EvaluationSplit.REGRESSION: _normalise_ids(regression_task_ids, field="regression_task_ids"),
        }
        seen: dict[str, EvaluationSplit] = {}
        for split, task_ids in partitions.items():
            for task_id in task_ids:
                previous = seen.get(task_id)
                if previous is not None:
                    raise ValueError(
                        f"task {task_id!r} appears in both {previous.value!r} and {split.value!r}"
                    )
                seen[task_id] = split
        self._partitions = partitions
        self._split_by_task = seen
        metadata = task_metadata or {}
        unknown_metadata = sorted(set(metadata) - set(seen))
        if unknown_metadata:
            raise ValueError(f"metadata contains unknown task ids: {unknown_metadata}")
        self._metadata = {task_id: dict(metadata.get(task_id, {})) for task_id in seen}

    @classmethod
    def from_train_test(
        cls,
        train_task_ids: Iterable[str],
        test_task_ids: Iterable[str],
        *,
        regression_task_ids: Iterable[str] = (),
    ) -> "EvaluationRegistry":
        """Compatibility constructor for the retained train/test bench API."""

        return cls(
            development_task_ids=train_task_ids,
            holdout_task_ids=test_task_ids,
            regression_task_ids=regression_task_ids,
        )

    def split_for(self, task_id: str) -> EvaluationSplit:
        try:
            return self._split_by_task[str(task_id).strip()]
        except KeyError as exc:
            raise RegistryAccessError(f"unknown evaluation task: {task_id!r}") from exc

    def task_ids(
        self,
        split: EvaluationSplit | str,
        *,
        consumer: str = _Consumer.OPTIMIZER,
    ) -> list[str]:
        parsed = EvaluationSplit.parse(split)
        consumer_name = str(consumer).strip().lower()
        self._check_split_access(parsed, consumer_name)
        return list(self._partitions[parsed])

    def view(
        self,
        split: EvaluationSplit | str,
        *,
        consumer: str = _Consumer.OPTIMIZER,
    ) -> SplitView:
        parsed = EvaluationSplit.parse(split)
        ids = tuple(self.task_ids(parsed, consumer=consumer))
        return SplitView(parsed, ids, str(consumer), redacted=True)

    def validate_task_ids(
        self,
        split: EvaluationSplit | str,
        task_ids: Iterable[str],
        *,
        consumer: str = _Consumer.OPTIMIZER,
    ) -> tuple[str, ...]:
        parsed = EvaluationSplit.parse(split)
        self._check_split_access(parsed, str(consumer).strip().lower())
        allowed = set(self._partitions[parsed])
        requested = _normalise_ids(task_ids, field="task_ids")
        unknown = sorted(set(requested) - allowed)
        if unknown:
            if consumer == _Consumer.OPTIMIZER and any(task_id in self._split_by_task for task_id in unknown):
                raise HoldoutLeakError(
                    f"optimizer requested task ids outside development: {unknown}; "
                    "holdout/regression material must remain outside optimization context"
                )
            raise RegistryAccessError(
                f"task ids {unknown} do not belong to the {parsed.value} split"
            )
        return requested

    def task(self, task_id: str, *, consumer: str = _Consumer.OPTIMIZER) -> EvaluationTask:
        split = self.split_for(task_id)
        self._check_split_access(split, str(consumer).strip().lower())
        return EvaluationTask(task_id, split, self._metadata[task_id])

    def metadata(self, task_id: str, *, consumer: str = _Consumer.OPTIMIZER) -> dict[str, Any]:
        """Return only non-sensitive metadata after split access is checked."""

        self.task(task_id, consumer=consumer)
        return dict(self._metadata[task_id])

    def assert_optimizer_safe(self, task_ids: Iterable[str]) -> tuple[str, ...]:
        """Validate that an optimization context contains development IDs only."""

        return self.validate_task_ids(
            EvaluationSplit.DEVELOPMENT,
            task_ids,
            consumer=_Consumer.OPTIMIZER,
        )

    @staticmethod
    def _check_split_access(split: EvaluationSplit, consumer: str) -> None:
        if consumer == _Consumer.OPTIMIZER:
            if split is not EvaluationSplit.DEVELOPMENT:
                raise HoldoutLeakError(
                    f"optimizer cannot access {split.value} evidence; "
                    "holdout/regression material must remain outside optimization context"
                )
            return
        if consumer == _Consumer.SEALED_EVALUATOR and split is not EvaluationSplit.HOLDOUT:
            raise RegistryAccessError("sealed evaluator may access holdout only")
        if consumer == _Consumer.REGRESSION_EVALUATOR and split is not EvaluationSplit.REGRESSION:
            raise RegistryAccessError("regression evaluator may access regression only")
        if consumer == _Consumer.HUMAN_REVIEW:
            return
        if consumer not in {
            _Consumer.SEALED_EVALUATOR,
            _Consumer.REGRESSION_EVALUATOR,
            _Consumer.HUMAN_REVIEW,
        }:
            raise RegistryAccessError(f"unknown evaluation consumer: {consumer!r}")

    def snapshot(self) -> dict[str, list[str]]:
        """Return durable IDs only; no task body or holdout metadata is exposed."""

        return {split.value: list(ids) for split, ids in self._partitions.items()}


__all__ = [
    "EvaluationRegistry",
    "EvaluationSplit",
    "EvaluationTask",
    "HoldoutLeakError",
    "RegistryAccessError",
    "SplitView",
]
