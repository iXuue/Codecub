"""Select embedded execution or the existing durable RunQueue."""

from __future__ import annotations

from .contracts import Run, RunStatus, TurnOutcome, TurnRequest


class EmbeddedExecutionBroker:
    def __init__(self, runner):
        self.runner = runner

    def submit(self, request: TurnRequest, run: Run):
        return self.runner.run(request, run)


class DurableExecutionBroker:
    """Adds spine correlation fields without replacing queue semantics."""

    durable = True

    def __init__(self, queue):
        self.queue = queue

    def submit(self, request: TurnRequest, run: Run) -> TurnOutcome:
        accepted = self.queue.enqueue(
            {
                "run_id": run.run_id,
                "turn_id": request.turn_id,
                "session_id": request.session_id,
                "conversation_id": request.conversation_id,
                "trace_id": request.trace_id,
                "origin": request.origin.value,
                "source": request.source.__dict__,
                "workspace": request.workspace,
                "task": request.message,
            }
        )
        if not accepted:
            return TurnOutcome(request.turn_id, run.run_id, RunStatus.FAILED, error="run enqueue rejected")
        return TurnOutcome(request.turn_id, run.run_id, RunStatus.QUEUED)
