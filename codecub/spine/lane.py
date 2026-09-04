from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from threading import Lock, Thread

from .cancellation import CancellationSource
from .contracts import Run, RunStatus, TurnOutcome, TurnRequest, new_id


class InjectionMailbox:
    def __init__(self):
        self._items: deque[TurnRequest] = deque()
        self._lock = Lock()
        self._submitted = 0
        self._drained = 0
        self._fallback_appended = 0

    def put(self, request: TurnRequest) -> None:
        with self._lock:
            self._items.append(request)
            self._submitted += 1

    def drain(self) -> list[TurnRequest]:
        with self._lock:
            items = list(self._items)
            self._items.clear()
            self._drained += len(items)
            return items

    def drain_for_fallback(self) -> list[TurnRequest]:
        with self._lock:
            items = list(self._items)
            self._items.clear()
            self._fallback_appended += len(items)
            return items

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "submitted": self._submitted,
                "drained": self._drained,
                "fallback_appended": self._fallback_appended,
            }


class ConversationLane:
    """Serializes turns for one conversation while other lanes may run."""

    def __init__(self, conversation_id: str, dispatch, on_injection_fallback=None):
        self.conversation_id = conversation_id
        self._dispatch = dispatch
        self._on_injection_fallback = on_injection_fallback or (lambda _request: None)
        self._lock = Lock()
        self._active: tuple[TurnRequest, Run, Future, CancellationSource] | None = None
        self._pending: deque[tuple[TurnRequest, Future]] = deque()
        self.injections = InjectionMailbox()

    @property
    def active_run_id(self) -> str:
        with self._lock:
            return self._active[1].run_id if self._active else ""

    def cancel_run(self, run_id: str) -> bool:
        """Request cooperative cancellation for the active run with ``run_id``.

        The lane remains the sole owner of run state transitions.  Callers such
        as a gateway may address a run by id, but they cannot complete its
        future or mutate the agent directly.
        """
        with self._lock:
            if not self._active or self._active[1].run_id != str(run_id):
                return False
            _request, run, _future, cancellation = self._active
            if run.status.terminal:
                return False
            if run.status is RunStatus.RUNNING:
                run.transition_to(RunStatus.CANCEL_REQUESTED)
                cancellation.cancel()
                run.transition_to(RunStatus.CANCELLING)
            else:
                cancellation.cancel()
            return True

    def submit(self, request: TurnRequest, front: bool = False) -> Future:
        outcome = Future()
        with self._lock:
            if front:
                self._pending.appendleft((request, outcome))
            else:
                self._pending.append((request, outcome))
            self._start_next_locked()
        return outcome

    def interrupt_and_submit(self, request: TurnRequest) -> Future:
        """Request cooperative cancellation, then run the replacement next."""
        with self._lock:
            if self._active:
                _, run, _, cancellation = self._active
                if not run.status.terminal:
                    run.transition_to(RunStatus.CANCEL_REQUESTED)
                    cancellation.cancel()
                    run.transition_to(RunStatus.CANCELLING)
            outcome = Future()
            self._pending.appendleft((request, outcome))
            self._start_next_locked()
            return outcome

    def inject(self, request: TurnRequest) -> bool:
        """Deliver to a live turn; callers append when this reports False."""
        with self._lock:
            if not self._active:
                return False
            self.injections.put(request)
            return True

    def drain_injections(self) -> list[TurnRequest]:
        return self.injections.drain()

    def injection_stats(self) -> dict[str, int]:
        return self.injections.snapshot()

    def _start_next_locked(self) -> None:
        if self._active or not self._pending:
            return
        request, outcome = self._pending.popleft()
        run_id = str(request.runtime_extensions.get("run_id") or new_id("run"))
        run = Run(run_id, request.turn_id, request.conversation_id, request.session_id, request.trace_id)
        cancellation = CancellationSource()
        request.runtime_extensions["cancellation_token"] = cancellation.token
        future = self._dispatch(request, run, self)
        self._active = (request, run, future, cancellation)
        Thread(target=self._wait_for_completion, args=(run, outcome, future, cancellation), daemon=True).start()

    def _wait_for_completion(self, run: Run, outcome: Future, completed: Future, cancellation: CancellationSource) -> None:
        self._finish(run, outcome, completed, cancellation)

    def _finish(self, run: Run, outcome: Future, completed: Future, cancellation: CancellationSource) -> None:
        try:
            result = completed.result()
        except Exception as exc:
            result = TurnOutcome(run.turn_id, run.run_id, RunStatus.FAILED, error=str(exc))
        if cancellation.token.cancelled:
            result = TurnOutcome(run.turn_id, run.run_id, RunStatus.CANCELLED, answer=result.answer, error=result.error)
        if result.status is not RunStatus.QUEUED:
            run.transition_to(result.status)
        outcome.set_result(result)
        with self._lock:
            if self._active and self._active[1].run_id == run.run_id:
                self._active = None
            for request in self.injections.drain_for_fallback():
                self._pending.append((request, Future()))
                self._on_injection_fallback(request)
            self._start_next_locked()
