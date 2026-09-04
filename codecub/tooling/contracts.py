"""Small, surface-neutral contracts for tool capability and execution metadata.

The contracts deliberately describe a tool call; they do not grant permission to
execute it.  CodeCub's existing approval, read-only, replay, and workspace
governance remains the authority at the execution boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ToolEffect(str, Enum):
    """The broad effect class a tool declares for scheduling and evidence."""

    UNKNOWN = "unknown"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class ToolCapability:
    """Declarative tool metadata; it is not an authorization decision."""

    effect: ToolEffect = ToolEffect.UNKNOWN
    concurrency_safe: bool = False
    idempotent: bool = False
    retryable: bool = False

    @classmethod
    def from_legacy(
        cls,
        tool: Mapping[str, Any] | None,
        *,
        name: str = "",
    ) -> "ToolCapability":
        """Derive a capability from CodeCub's existing tool-spec fields."""

        tool = tool or {}
        raw_effect = tool.get("effect")
        if isinstance(raw_effect, ToolEffect):
            effect = raw_effect
        elif raw_effect:
            try:
                effect = ToolEffect(str(raw_effect).strip().lower())
            except ValueError:
                effect = ToolEffect.UNKNOWN
        elif tool.get("risky"):
            effect = (
                ToolEffect.EXECUTE
                if name == "run_shell"
                else ToolEffect.WRITE
            )
        else:
            effect = ToolEffect.READ

        read_only = effect is ToolEffect.READ and not bool(tool.get("risky"))
        return cls(
            effect=effect,
            concurrency_safe=bool(tool.get("concurrency_safe", read_only)),
            idempotent=bool(tool.get("idempotent", read_only)),
            retryable=bool(tool.get("retryable", read_only)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.value,
            "concurrency_safe": self.concurrency_safe,
            "idempotent": self.idempotent,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """One call carrier that can cross execution, tracing, and queue seams."""

    name: str
    arguments: dict[str, Any]
    call_id: str = ""
    session_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    origin: str = "USER"
    iteration: int = 0
    parent_call_id: str = ""
    operation_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "arguments", dict(self.arguments or {}))
        object.__setattr__(self, "origin", str(self.origin or "USER"))
        object.__setattr__(self, "iteration", max(0, int(self.iteration or 0)))

    def to_metadata(self) -> dict[str, Any]:
        """Return non-sensitive correlation fields suitable for trace metadata."""

        return {
            "tool_call_id": self.call_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "origin": self.origin,
            "iteration": self.iteration,
            "parent_call_id": self.parent_call_id,
            "operation_key": self.operation_key,
        }


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """Observed result of one invocation, without changing result semantics."""

    invocation: ToolInvocation
    result: Any
    duration_ms: float
