"""Deterministic failure mining from existing traces.

The miner intentionally does not add a terminal state.  The retained five
terminal outcome kinds remain the source of truth; this module derives a more
specific reason and a stable behaviour signature from trace evidence around
that outcome.

It accepts the JSON-like records emitted by the current tracing stores as well
as small test/future adapters.  Raw trace text is never returned in the
optimization summary; only bounded, normalized evidence labels are retained.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from pico.evolver.evaluation.registry import EvaluationRegistry, EvaluationSplit, HoldoutLeakError

# These are existing LoopOutcome kinds in the retained runtime.  They are
# documented here as a compatibility boundary, not as a new state machine.
PRESERVED_TURN_TERMINAL_KINDS = frozenset(
    {"success", "limited", "model_error", "cancelled", "finalization_failed"}
)

_ERROR_WORDS = re.compile(
    r"\b(timeout|timed out|error|exception|failed|failure|denied|rejected|invalid|parse|rate limit|network)\b",
    re.IGNORECASE,
)
_TOOL_NAME = re.compile(r"(?:tool(?:\.name)?|name)\s*[:=]\s*[\"']?([A-Za-z0-9_.:/-]+)", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class FailureEvidence:
    """A bounded pointer/label, without raw holdout or full trace content."""

    trace_id: str
    turn_index: int | None
    signal: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "turn_index": self.turn_index,
            "signal": self.signal,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class FailureFinding:
    trace_id: str
    task_id: str
    terminal_kind: str
    stop_reason: str
    failure_reason: str
    behavior_signature: str
    evidence: tuple[FailureEvidence, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "terminal_kind": self.terminal_kind,
            "stop_reason": self.stop_reason,
            "failure_reason": self.failure_reason,
            "behavior_signature": self.behavior_signature,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class FailureMiningReport:
    findings: tuple[FailureFinding, ...]
    signature_counts: dict[str, int]
    reason_counts: dict[str, int]
    terminal_counts: dict[str, int]

    def optimization_context(self) -> dict[str, Any]:
        """Return summary-only input safe for the proposal generator."""

        return {
            "signature_counts": dict(self.signature_counts),
            "reason_counts": dict(self.reason_counts),
            "terminal_counts": dict(self.terminal_counts),
            "findings": [
                {
                    "trace_id": finding.trace_id,
                    "task_id": finding.task_id,
                    "failure_reason": finding.failure_reason,
                    "behavior_signature": finding.behavior_signature,
                    "evidence": [item.to_dict() for item in finding.evidence],
                }
                for finding in self.findings
            ],
        }


def _value(record: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        for key in keys:
            if key in record:
                return record[key]
    for key in keys:
        if hasattr(record, key):
            return getattr(record, key)
    return default


def _normalise_record(record: Any) -> Any:
    """Adapt the retained ``TrajectorySource`` tuple without changing it."""

    if isinstance(record, (tuple, list)) and len(record) >= 3:
        trace_id, task_id, text = record[0], record[1], record[2]
        body = _text(text, limit=4000)
        lower = body.lower()
        if "model error" in lower or "exception" in lower:
            kind, reason = "model_error", "model_error"
        elif any(marker in lower for marker in ("step limit", "stopped", "timed out")):
            kind, reason = "limited", "step_limit_reached"
        else:
            kind, reason = "failed", "trajectory_failure"
        return {
            "trace_id": trace_id,
            "task_id": task_id,
            "kind": kind,
            "stop_reason": reason,
            "events": [{"message": body}],
        }
    return record


def _text(value: Any, *, limit: int = 800) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        result = value
    else:
        try:
            result = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            result = str(value)
    return _WHITESPACE.sub(" ", result).strip()[:limit]


def _events(record: Any) -> list[Any]:
    raw = _value(record, "events", "spans", "trace", "steps", default=[])
    if isinstance(raw, Mapping):
        return [raw]
    if isinstance(raw, (str, bytes)):
        return [raw]
    try:
        return list(raw or [])
    except TypeError:
        return [raw]


def _terminal_kind(record: Any) -> str:
    raw = _value(record, "terminal_kind", "kind", "outcome_kind", "status", default="unknown")
    return str(raw or "unknown").strip().lower()


def _stop_reason(record: Any) -> str:
    raw = _value(record, "stop_reason", "reason", "error_code", "failure_reason", default="")
    return _text(raw, limit=160).lower().replace(" ", "_") or "unspecified"


def _event_text(event: Any) -> str:
    return _text(
        _value(event, "message", "error", "detail", "name", "attributes", "payload", default=event),
        limit=1200,
    )


def _extract_signals(record: Any) -> tuple[list[str], list[FailureEvidence]]:
    terminal = _terminal_kind(record)
    reason = _stop_reason(record)
    trace_id = _text(_value(record, "trace_id", "traceId", "id", default="unknown"), limit=120)
    signals: list[str] = []
    evidence: list[FailureEvidence] = []
    if terminal in {"model_error", "error", "failed"} or "error" in reason or "exception" in reason:
        signals.append("model_or_provider_error")
        evidence.append(FailureEvidence(trace_id, None, "terminal", reason))
    if terminal in {"limited", "stuck", "emergency_cap"} or any(
        marker in reason for marker in ("limit", "stuck", "cap", "timeout")
    ):
        signals.append("no_convergence_before_budget")
        evidence.append(FailureEvidence(trace_id, None, "terminal", reason))
    if terminal == "cancelled" or "cancel" in reason:
        signals.append("cancelled_before_completion")
        evidence.append(FailureEvidence(trace_id, None, "terminal", reason))
    if terminal == "finalization_failed" or "final" in reason:
        signals.append("finalization_or_submission_failure")
        evidence.append(FailureEvidence(trace_id, None, "terminal", reason))

    tool_names: list[str] = []
    tool_errors = 0
    verification_errors = 0
    empty_outputs = 0
    previous_tool = None
    repeated_tool = False
    for index, event in enumerate(_events(record)):
        text = _event_text(event)
        lower = text.lower()
        match = _TOOL_NAME.search(text)
        if match:
            tool = match.group(1).lower()
            tool_names.append(tool)
            if previous_tool == tool:
                repeated_tool = True
            previous_tool = tool
        if _ERROR_WORDS.search(text) and any(marker in lower for marker in ("tool", "execute", "call")):
            tool_errors += 1
            evidence.append(FailureEvidence(trace_id, index, "tool_error", _text(text, limit=240)))
        if any(marker in lower for marker in ("verify", "validation", "assert", "grader")) and _ERROR_WORDS.search(text):
            verification_errors += 1
            evidence.append(FailureEvidence(trace_id, index, "verification_error", _text(text, limit=240)))
        if any(marker in lower for marker in ("empty response", "no output", "blank response")):
            empty_outputs += 1
            evidence.append(FailureEvidence(trace_id, index, "empty_output", "empty model output"))
    if tool_errors:
        signals.append("tool_execution_error")
    if verification_errors:
        signals.append("verification_failure")
    if repeated_tool:
        signals.append("repeated_tool_behavior")
    if not tool_names and terminal not in {"success", "unknown"}:
        signals.append("no_tool_progress")
    if empty_outputs:
        signals.append("empty_response")
    if not signals:
        signals.append("terminal_failure" if terminal not in {"success", "unknown"} else "completed")
    return sorted(set(signals)), evidence[:8]


def _reason(signals: list[str], terminal: str, stop_reason: str) -> str:
    priority = (
        "verification_failure",
        "tool_execution_error",
        "finalization_or_submission_failure",
        "model_or_provider_error",
        "no_convergence_before_budget",
        "repeated_tool_behavior",
        "empty_response",
        "no_tool_progress",
        "cancelled_before_completion",
    )
    for signal in priority:
        if signal in signals:
            return signal
    if terminal == "success":
        return "completed_without_failure"
    return stop_reason or "terminal_failure"


def _signature(signals: list[str], terminal: str, stop_reason: str) -> str:
    material = json.dumps(
        {"signals": signals, "terminal": terminal, "stop_reason": stop_reason},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "behavior_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class FailureMiner:
    """Mine failure reasons without widening the runtime terminal-state set."""

    def __init__(self, registry: EvaluationRegistry | None = None) -> None:
        self.registry = registry

    def mine(
        self,
        records: Iterable[Any],
        *,
        split: EvaluationSplit | str = EvaluationSplit.DEVELOPMENT,
        consumer: str = "optimizer",
        task_id_resolver: Callable[[Any], str] | None = None,
    ) -> FailureMiningReport:
        """Extract bounded failure findings from trace records.

        ``task_id_resolver`` is an adapter for legacy trajectory tuples whose
        second element is a human-readable task description rather than the
        registry ID.  The default keeps the generic tuple contract unchanged;
        benchmark adapters must provide the resolver when needed.
        """

        parsed_split = EvaluationSplit.parse(split)
        if self.registry is not None:
            if consumer == "optimizer" and parsed_split is not EvaluationSplit.DEVELOPMENT:
                raise HoldoutLeakError(
                    f"Failure Miner optimizer context cannot read {parsed_split.value} traces"
                )
            allowed = set(self.registry.task_ids(parsed_split, consumer=consumer))
        else:
            allowed = None

        findings: list[FailureFinding] = []
        for raw_record in records:
            record = _normalise_record(raw_record)
            resolved_task_id = (
                task_id_resolver(raw_record)
                if task_id_resolver is not None
                else _value(record, "task_id", "taskId", default="unknown")
            )
            task_id = _text(resolved_task_id, limit=160)
            if allowed is not None and task_id not in allowed:
                raise HoldoutLeakError(
                    f"trace task {task_id!r} is not registered in {parsed_split.value} split"
                )
            trace_id = _text(_value(record, "trace_id", "traceId", "id", default=task_id), limit=120)
            terminal = _terminal_kind(record)
            stop_reason = _stop_reason(record)
            signals, evidence = _extract_signals(record)
            findings.append(
                FailureFinding(
                    trace_id=trace_id,
                    task_id=task_id,
                    terminal_kind=terminal,
                    stop_reason=stop_reason,
                    failure_reason=_reason(signals, terminal, stop_reason),
                    behavior_signature=_signature(signals, terminal, stop_reason),
                    evidence=tuple(evidence),
                )
            )
        return FailureMiningReport(
            findings=tuple(findings),
            signature_counts=dict(Counter(item.behavior_signature for item in findings)),
            reason_counts=dict(Counter(item.failure_reason for item in findings)),
            terminal_counts=dict(Counter(item.terminal_kind for item in findings)),
        )


__all__ = [
    "FailureEvidence",
    "FailureFinding",
    "FailureMiner",
    "FailureMiningReport",
    "PRESERVED_TURN_TERMINAL_KINDS",
]
