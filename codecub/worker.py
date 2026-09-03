"""Execute complete agent runs from a queue in one worker process."""

from __future__ import annotations

from collections import deque
from threading import Event, Lock, Thread


class AgentRunWorker:
    def __init__(self, queue, agent_factory, control_bus=None):
        self.queue = queue
        self.agent_factory = agent_factory
        self.control_bus = control_bus
        self._lock = Lock()
        self._active_agents = {}
        self._mailboxes = {}
        self._cancelled = set()

    def apply_control(self, message):
        """Apply RUN_* controls to a currently executing worker-owned Run."""
        def apply(item):
            agent = None
            with self._lock:
                if item.run_id not in self._active_agents:
                    return
                agent = self._active_agents[item.run_id]
                if item.type == "RUN_INJECT":
                    self._mailboxes[item.run_id].append(str(item.payload.get("message", "")))
                elif item.type in {"RUN_INTERRUPT", "RUN_CANCEL"}:
                    self._cancelled.add(item.run_id)
                elif item.type == "APPROVAL_RESOLVE":
                    resolver = getattr(agent, "resolve_approval", None)
                    if resolver is not None:
                        resolver(item.payload)
            self._record_control(agent, item)

        if self.control_bus is None:
            apply(message)
            return True
        return self.control_bus.apply_once(message, apply)

    def poll_control_once(self):
        """Read one Redis-delivered control message when the bus supports it."""
        consume = getattr(self.control_bus, "consume_one", None)
        if consume is None:
            return False
        item = consume()
        if item is None:
            return False
        message_id, message = item
        self.apply_control(message)
        self.control_bus.ack(message_id)
        return True

    def run_once(self):
        envelope = self.queue.dequeue()
        if envelope is None:
            return None
        run_id = envelope["run_id"]
        agent = self.agent_factory(envelope)
        mailbox = deque()
        with self._lock:
            self._active_agents[run_id] = agent
            self._mailboxes[run_id] = mailbox
        agent.injection_provider = lambda: self._drain_mailbox(run_id)
        agent.cancel_checker = lambda _runtime, task_state: bool(
            getattr(task_state, "run_id", "") in self._cancelled
        )
        control_stop = Event()
        control_thread = None
        if callable(getattr(self.control_bus, "consume_one", None)):
            control_thread = Thread(target=self._control_loop, args=(control_stop,), daemon=True)
            control_thread.start()
        answer = ""
        error = None
        try:
            answer = agent.ask(envelope["task"], run_id=run_id)
        except Exception as exc:
            error = exc
        finally:
            control_stop.set()
            if control_thread is not None:
                control_thread.join(timeout=1)
            with self._lock:
                was_cancelled = run_id in self._cancelled
                self._active_agents.pop(run_id, None)
                self._mailboxes.pop(run_id, None)
                self._cancelled.discard(run_id)
        if error is not None and not was_cancelled:
            self.queue.fail(run_id, error)
            return {"run_id": run_id, "status": "failed", "error": str(error)}
        self.queue.complete(run_id)
        if was_cancelled:
            result = {"run_id": run_id, "status": "cancelled", "answer": answer}
            if error is not None:
                result["error"] = str(error)
            return result
        return {"run_id": run_id, "status": "completed", "answer": answer}

    def _control_loop(self, stop: Event) -> None:
        while not stop.is_set():
            if not self.poll_control_once():
                stop.wait(0.01)

    def _drain_mailbox(self, run_id):
        with self._lock:
            mailbox = self._mailboxes.get(run_id)
            if mailbox is None:
                return []
            items = list(mailbox)
            mailbox.clear()
            return items

    @staticmethod
    def _record_control(agent, message):
        emit_trace = getattr(agent, "emit_trace", None)
        task_state = getattr(agent, "current_task_state", None)
        if callable(emit_trace) and task_state is not None:
            emit_trace(task_state, "control_applied", {
                "control_id": message.control_id,
                "control_type": message.type,
                "trace_id": message.trace_id,
                "session_id": message.session_id,
                "conversation_id": message.conversation_id,
                "turn_id": message.turn_id,
                "run_id": message.run_id,
            })
