"""Central ToolExecutor seam preserving legacy validation and replay behavior."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import time

from .contracts import ToolCapability, ToolExecution, ToolInvocation


@dataclass(frozen=True)
class ToolExecutionContext:
    """Narrow collaborators consumed by the tool-governance owner."""

    registry: object
    validation: object
    approval: object
    replay: object
    cancellation: object
    workspace: object
    observation: object
    hook_subject: object
    session_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    origin: str = "USER"
    iteration: int = 0
    parent_call_id: str = ""
    authorization: object | None = None


def _capability_for(tool, name: str) -> ToolCapability:
    if isinstance(tool, Mapping):
        declared = tool.get("capability")
        if isinstance(declared, ToolCapability):
            return declared
    return ToolCapability.from_legacy(
        tool if isinstance(tool, Mapping) else {}, name=name
    )


def _execution_metadata(tool, name: str, invocation: ToolInvocation | None) -> dict:
    capability = _capability_for(tool, name)
    metadata = {
        "tool_effect": capability.effect.value,
        "tool_concurrency_safe": capability.concurrency_safe,
        "tool_idempotent": capability.idempotent,
        "tool_retryable": capability.retryable,
    }
    if invocation is not None:
        metadata.update(invocation.to_metadata())
    return metadata


class GovernedToolExecutor:
    """Standalone tool-governance pipeline with no Runtime dependency."""

    def __init__(self, context, hooks):
        self.context = context
        self.hooks = hooks
        self.last_metadata = {}
        self.last_invocation = None

    def validate(self, name, args):
        """Validate one prospective call without approval, execution, or hooks."""
        name, args = str(name or "").strip(), dict(args or {})
        tool = self.context.registry.resolve(name)
        if tool is None:
            raise KeyError(name)
        self.context.validation.validate(name, args, tool)
        return tool

    def execute(self, name, args, operation_key="", *, invocation=None):
        name, args = str(name or "").strip(), dict(args or {})
        invocation = invocation or ToolInvocation(
            name=name,
            arguments=args,
            operation_key=str(operation_key or "").strip(),
            session_id=self.context.session_id,
            conversation_id=self.context.conversation_id,
            turn_id=self.context.turn_id,
            origin=self.context.origin,
            iteration=self.context.iteration,
            parent_call_id=self.context.parent_call_id,
        )
        self.last_invocation = invocation
        self.hooks.before_tool(self.context.hook_subject, name=name, args=args, operation_key=operation_key)
        try:
            result = self._execute(
                name,
                args,
                str(operation_key or "").strip(),
                invocation=invocation,
            )
        except Exception as exc:
            self.hooks.on_error(self.context.hook_subject, error=exc, tool_name=name)
            raise
        self.hooks.after_tool(self.context.hook_subject, name=name, args=args, result=result, operation_key=operation_key)
        return result

    def _reject(self, name, code, message, tool=None, invocation=None, **extra):
        metadata = {
            "tool_status": "rejected",
            "tool_error_code": code,
            "security_event_type": (
                "path_escape" if "path escapes workspace" in str(message)
                else "capability_denied" if code == "capability_denied"
                else "read_only_block" if code == "approval_denied" and getattr(self.context.approval, "read_only", False)
                else "approval_denied" if code == "approval_denied" else ""
            ),
            "risk_level": "high" if tool and tool.get("risky") else "low",
            "read_only": not bool(tool and tool.get("risky")),
            "affected_paths": [],
            "workspace_changed": False,
            "diff_summary": [],
            **_execution_metadata(tool, name, invocation),
            **extra,
        }
        self.last_metadata = metadata
        self.context.observation.record(name, {}, message, metadata)
        return message

    def _execute(self, name, args, operation_key, *, invocation=None):
        tool = self.context.registry.resolve(name)
        if tool is None:
            return self._reject(
                name,
                "unknown_tool",
                f"error: unknown tool '{name}'",
                invocation=invocation,
            )
        if self.context.cancellation.requested():
            return self._reject(
                name,
                "cancelled",
                f"error: tool {name} cancelled",
                tool,
                invocation,
            )
        authorizer = getattr(self.context, "authorization", None)
        authorize = getattr(authorizer, "allow", None)
        if callable(authorize) and not authorize(name, tool, invocation):
            return self._reject(
                name,
                "capability_denied",
                f"error: capability denied for {name}",
                tool,
                invocation,
            )
        try:
            self.context.validation.validate(name, args, tool)
        except Exception as exc:
            example = self.context.validation.example(name)
            message = f"error: invalid arguments for {name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            return self._reject(name, "invalid_arguments", message, tool, invocation)
        prepare = getattr(self.context.observation, "prepare", None)
        prepared = prepare(name, args, tool) if prepare else {}
        if prepared.get("result") is not None:
            metadata = dict(prepared.get("metadata") or {})
            metadata.setdefault("tool_status", "ok")
            metadata.setdefault("tool_error_code", "")
            metadata.setdefault("risk_level", "low")
            metadata.setdefault("read_only", True)
            metadata.setdefault("affected_paths", [])
            metadata.setdefault("workspace_changed", False)
            metadata.setdefault("diff_summary", [])
            metadata.update(_execution_metadata(tool, name, invocation))
            self.last_metadata = metadata
            result = prepared["result"]
            self.context.observation.record(name, args, result, metadata)
            return result
        repeated = getattr(self.context.replay, "repeated", None)
        if repeated and repeated(name, args):
            return self._reject(
                name,
                "repeated_identical_call",
                f"error: repeated identical tool call for {name}; same no-progress action reached {self.context.replay.limit} consecutive attempts",
                tool,
                invocation,
            )
        if tool.get("risky") and not self.context.approval.approve(name, args):
            return self._reject(
                name,
                "approval_denied",
                f"error: approval denied for {name}",
                tool,
                invocation,
            )
        if tool.get("circuit_breaker", True) and not self.context.replay.allow(name):
            return self._reject(
                name,
                "circuit_open",
                f"error: tool {name} circuit is open",
                tool,
                invocation,
                tool_status="blocked",
                circuit_state=self.context.replay.status(name),
            )
        claim = self.context.replay.claim(name, args, operation_key, bool(tool.get("side_effect")))
        if not claim.get("claimed", True):
            return self._reject(
                name,
                claim["error_code"],
                claim["message"],
                tool,
                invocation,
                tool_status="blocked",
                **dict(claim.get("metadata") or {}),
            )
        before = self.context.workspace.snapshot() if tool.get("risky") else {}
        try:
            calls = prepared.get("calls") or (args,)
            results = [str(tool["run"](call_args)) for call_args in calls]
            result = prepared.get("format_result", lambda values: values[0] if len(values) == 1 else "\n\n".join(values))(results)
            after = self.context.workspace.snapshot() if tool.get("risky") else before
            affected, diff = self.context.workspace.diff(before, after)
            business_success, reason = self.context.validation.validate_result(tool, result)
            metadata = {
                "tool_status": "ok" if business_success else ("partial_success" if affected else "error"),
                "tool_error_code": "" if business_success else "tool_business_failure",
                "tool_execution_success": True,
                "tool_business_success": business_success,
                "tool_result_validation_failed": not business_success,
                "tool_result_failure_reason": reason,
                "risk_level": "high" if tool.get("risky") else "low",
                "read_only": not tool.get("risky"),
                "affected_paths": affected,
                "workspace_changed": bool(affected),
                "workspace_fingerprint": self.context.workspace.fingerprint(),
                "diff_summary": diff,
            }
            metadata.update(_execution_metadata(tool, name, invocation))
            self.last_metadata = metadata
            self.context.replay.complete(claim, business_success, metadata)
            self.context.replay.record_result(name, business_success)
            finalize = getattr(self.context.observation, "finalize", None)
            if finalize:
                finalize(name, args, result, metadata, prepared)
            self.context.observation.record(name, args, result, metadata)
            return result
        except Exception as exc:
            after = self.context.workspace.snapshot() if tool.get("risky") else before
            affected, diff = self.context.workspace.diff(before, after)
            metadata = {
                "tool_status": "partial_success" if affected else "error",
                "tool_error_code": "tool_partial_success" if affected else "tool_failed",
                "tool_execution_success": False,
                "tool_business_success": False,
                "risk_level": "high" if tool.get("risky") else "low",
                "read_only": not tool.get("risky"),
                "affected_paths": affected,
                "workspace_changed": bool(affected),
                "workspace_fingerprint": self.context.workspace.fingerprint(),
                "diff_summary": diff,
            }
            metadata.update(_execution_metadata(tool, name, invocation))
            self.last_metadata = metadata
            self.context.replay.complete(claim, False, metadata)
            self.context.replay.record_result(name, False)
            result = f"error: tool {name} failed: {exc}"
            self.context.observation.record(name, args, result, metadata)
            return result

class ToolExecutor:
    """Production tool-governance owner.

    ``context`` is deliberately a collection of narrow collaborators.  In
    particular, this boundary must never receive a Pico/Runtime instance and
    call its legacy tool entry point.
    """

    def __init__(self, context, hooks):
        self.context = context
        self.hooks = hooks
        self.last_metadata = {}
        self.last_invocation = None
        self.last_execution = None

    def execute(
        self,
        name,
        args,
        operation_key="",
        *,
        call_id="",
        session_id=None,
        conversation_id=None,
        turn_id=None,
        origin=None,
        iteration=None,
        parent_call_id=None,
    ):
        name, args = str(name or "").strip(), dict(args or {})
        normalized_operation_key = str(operation_key or "").strip()
        invocation = ToolInvocation(
            name=name,
            arguments=args,
            call_id=str(call_id or ""),
            session_id=(self.context.session_id if session_id is None else str(session_id)),
            conversation_id=(
                self.context.conversation_id
                if conversation_id is None
                else str(conversation_id)
            ),
            turn_id=self.context.turn_id if turn_id is None else str(turn_id),
            origin=self.context.origin if origin is None else str(origin),
            iteration=self.context.iteration if iteration is None else iteration,
            parent_call_id=(
                self.context.parent_call_id
                if parent_call_id is None
                else str(parent_call_id)
            ),
            operation_key=normalized_operation_key,
        )
        self.last_invocation = invocation
        started = time.perf_counter_ns()
        subject = self.context.hook_subject
        self.hooks.before_tool(subject, name=name, args=args, operation_key=operation_key)
        try:
            governed = GovernedToolExecutor(self.context, self.hooks)
            result = governed._execute(
                name,
                args,
                normalized_operation_key,
                invocation=invocation,
            )
            observed = dict(
                getattr(self.context.observation, "last_metadata", {}) or {}
            )
            # The governed pipeline is the current-call source of truth;
            # observation adapters may retain a previous snapshot when they
            # only implement the historical ``record`` seam.
            self.last_metadata = {**observed, **governed.last_metadata}
            self.last_execution = ToolExecution(
                invocation=invocation,
                result=result,
                duration_ms=(time.perf_counter_ns() - started) / 1_000_000,
            )
        except Exception as exc:
            self.hooks.on_error(subject, error=exc, tool_name=name)
            raise
        self.hooks.after_tool(subject, name=name, args=args, result=result, operation_key=operation_key)
        return result
