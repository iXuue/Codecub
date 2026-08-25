"""Execute complete agent runs from a queue in one worker process."""

from __future__ import annotations


class AgentRunWorker:
    def __init__(self, queue, agent_factory):
        self.queue = queue
        self.agent_factory = agent_factory

    def run_once(self):
        envelope = self.queue.dequeue()
        if envelope is None:
            return None
        run_id = envelope["run_id"]
        try:
            answer = self.agent_factory(envelope).ask(
                envelope["task"], run_id=run_id
            )
        except Exception as exc:
            self.queue.fail(run_id, exc)
            return {"run_id": run_id, "status": "failed", "error": str(exc)}
        self.queue.complete(run_id)
        return {"run_id": run_id, "status": "completed", "answer": answer}
