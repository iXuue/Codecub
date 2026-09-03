"""Central ToolExecutor seam preserving legacy validation and replay behavior."""

from __future__ import annotations

from dataclasses import dataclass


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


class GovernedToolExecutor:
    """Standalone tool-governance pipeline with no Runtime dependency."""

    def __init__(self, context, hooks):
        self.context = context
        self.hooks = hooks

    def validate(self, name, args):
        """Validate one prospective call without approval, execution, or hooks."""
        name, args = str(name or "").strip(), dict(args or {})
        tool = self.context.registry.resolve(name)
        if tool is None:
            raise KeyError(name)
        self.context.validation.validate(name, args, tool)
        return tool
        self.last_metadata = {}

    def execute(self, name, args, operation_key=""):
        name, args = str(name or "").strip(), dict(args or {})
        self.hooks.before_tool(self.context.hook_subject, name=name, args=args, operation_key=operation_key)
        try:
            result = self._execute(name, args, str(operation_key or "").strip())
        except Exception as exc:
            self.hooks.on_error(self.context.hook_subject, error=exc, tool_name=name)
            raise
        self.hooks.after_tool(self.context.hook_subject, name=name, args=args, result=result, operation_key=operation_key)
        return result

    def _reject(self, name, code, message, tool=None, **extra):
        metadata = {
            "tool_status": "rejected",
            "tool_error_code": code,
            "security_event_type": (
                "path_escape" if "path escapes workspace" in str(message)
                else "read_only_block" if code == "approval_denied" and getattr(self.context.approval, "read_only", False)
                else "approval_denied" if code == "approval_denied" else ""
            ),
            "risk_level": "high" if tool and tool.get("risky") else "low",
            "read_only": not bool(tool and tool.get("risky")),
            "affected_paths": [],
            "workspace_changed": False,
            "diff_summary": [],
            **extra,
        }
        self.context.observation.record(name, {}, message, metadata)
        return message

    def _execute(self, name, args, operation_key):
        tool = self.context.registry.resolve(name)
        if tool is None:
            return self._reject(name, "unknown_tool", f"error: unknown tool '{name}'")
        if self.context.cancellation.requested():
            return self._reject(name, "cancelled", f"error: tool {name} cancelled", tool)
        try:
            self.context.validation.validate(name, args, tool)
        except Exception as exc:
            example = self.context.validation.example(name)
            message = f"error: invalid arguments for {name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            return self._reject(name, "invalid_arguments", message, tool)
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
            )
        if tool.get("risky") and not self.context.approval.approve(name, args):
            return self._reject(name, "approval_denied", f"error: approval denied for {name}", tool)
        if tool.get("circuit_breaker", True) and not self.context.replay.allow(name):
            return self._reject(name, "circuit_open", f"error: tool {name} circuit is open", tool,
                                tool_status="blocked", circuit_state=self.context.replay.status(name))
        claim = self.context.replay.claim(name, args, operation_key, bool(tool.get("side_effect")))
        if not claim.get("claimed", True):
            return self._reject(name, claim["error_code"], claim["message"], tool,
                                tool_status="blocked", **dict(claim.get("metadata") or {}))
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

    def execute(self, name, args, operation_key=""):
        name, args = str(name or "").strip(), dict(args or {})
        subject = self.context.hook_subject
        self.hooks.before_tool(subject, name=name, args=args, operation_key=operation_key)
        try:
            result = GovernedToolExecutor(self.context, self.hooks)._execute(
                name, args, str(operation_key or "").strip()
            )
            self.last_metadata = dict(
                getattr(self.context.observation, "last_metadata", {}) or {}
            )
        except Exception as exc:
            self.hooks.on_error(subject, error=exc, tool_name=name)
            raise
        self.hooks.after_tool(subject, name=name, args=args, result=result, operation_key=operation_key)
        return result
