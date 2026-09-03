"""Small structured-tracing core; exporters remain optional extension points."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    session_id: str
    conversation_id: str
    turn_id: str
    run_id: str = ""
    span_id: str = field(default_factory=lambda: uuid4().hex)

    def child(self, run_id: str = "") -> "TraceContext":
        return TraceContext(self.trace_id, self.session_id, self.conversation_id, self.turn_id, run_id or self.run_id)


@dataclass(frozen=True)
class TraceEvent:
    name: str
    context: TraceContext
    payload: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {"name": self.name, "timestamp": self.timestamp, "payload": dict(self.payload), **asdict(self.context)}


class TraceRecorder:
    def __init__(self, emit):
        self._emit = emit

    def record(self, name: str, context: TraceContext, payload: dict | None = None) -> TraceEvent:
        event = TraceEvent(name, context, dict(payload or {}))
        self._emit(event)
        return event
