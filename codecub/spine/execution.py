"""Execution boundary between conversation semantics and legacy Runtime."""

from __future__ import annotations

import inspect
from typing import Protocol

from .contracts import Run, RunStatus, TurnOutcome, TurnRequest


class TurnRunner(Protocol):
    def run(self, request: TurnRequest, run: Run) -> TurnOutcome: ...


class LegacyTurnRunner:
    """Compatibility adapter for the existing synchronous ``Pico.ask`` API."""

    def __init__(self, agent_factory):
        self.agent_factory = agent_factory

    def run(self, request: TurnRequest, run: Run) -> TurnOutcome:
        agent = self.agent_factory(request)
        agent.injection_provider = request.runtime_extensions.get("injection_provider")
        # The legacy runtime owns durable traces.  Pass the immutable Spine
        # correlation tuple explicitly; it must never be inferred from the
        # most recently active session or filesystem timestamps.
        agent.spine_trace_context = {
            "trace_id": request.trace_id,
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            "turn_id": request.turn_id,
            "run_id": run.run_id,
        }
        cancellation = request.runtime_extensions.get("cancellation_token")
        if cancellation is not None:
            agent.cancel_checker = lambda _runtime, _task_state: cancellation.cancelled
        try:
            if "run_id" in inspect.signature(agent.ask).parameters:
                answer = agent.ask(request.message, run_id=run.run_id)
            else:
                answer = agent.ask(request.message)
        except Exception as exc:
            if cancellation is not None and cancellation.cancelled:
                return TurnOutcome(request.turn_id, run.run_id, RunStatus.CANCELLED, error=str(exc))
            return TurnOutcome(request.turn_id, run.run_id, RunStatus.FAILED, error=str(exc))
        if cancellation is not None and cancellation.cancelled:
            return TurnOutcome(request.turn_id, run.run_id, RunStatus.CANCELLED)
        return TurnOutcome(request.turn_id, run.run_id, RunStatus.COMPLETED, answer=str(answer))
