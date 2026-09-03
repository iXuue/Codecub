"""Surface-neutral interaction contracts; UI adapters remain compatibility code."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock
from uuid import uuid4


@dataclass(frozen=True)
class InteractionRequest:
    interaction_id: str
    kind: str
    run_id: str
    payload: dict


class InteractionBroker:
    def __init__(self):
        self._lock = Lock()
        self._pending = {}

    def request(self, kind: str, run_id: str, payload: dict | None = None, interaction_id: str = "") -> InteractionRequest:
        request = InteractionRequest(interaction_id or uuid4().hex, kind, run_id, dict(payload or {}))
        with self._lock:
            self._pending[request.interaction_id] = (request, Event(), None)
        return request

    def resolve(self, interaction_id: str, value, run_id: str = "") -> bool:
        with self._lock:
            item = self._pending.get(interaction_id)
            if item is None:
                return False
            request, event, _ = item
            if run_id and request.run_id != run_id:
                return False
            if event.is_set():
                return False
            self._pending[interaction_id] = (request, event, value)
            event.set()
            return True

    def wait(self, request: InteractionRequest, timeout=None):
        with self._lock:
            item = self._pending.get(request.interaction_id)
        if item is None:
            raise KeyError("unknown interaction")
        _, event, _ = item
        if not event.wait(timeout):
            # A timed-out interaction must not remain resolvable later by a
            # stale UI/control event.
            with self._lock:
                self._pending.pop(request.interaction_id, None)
            raise TimeoutError("interaction timed out")
        with self._lock:
            _, _, value = self._pending.pop(request.interaction_id)
        return value

    def cancel_run(self, run_id: str, value=False) -> int:
        """Resolve all pending interactions belonging to one cancelled run."""
        with self._lock:
            interaction_ids = [key for key, (request, _event, _value) in self._pending.items() if request.run_id == run_id]
        return sum(self.resolve(interaction_id, value, run_id=run_id) for interaction_id in interaction_ids)


QuestionBroker = InteractionBroker
ConfirmationBroker = InteractionBroker
ApprovalBroker = InteractionBroker
