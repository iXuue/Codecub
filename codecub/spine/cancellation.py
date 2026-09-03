"""Cooperative cancellation primitives shared by runners and tools."""

from __future__ import annotations

from threading import Event, Lock


class CancellationError(RuntimeError):
    pass


class CancellationToken:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._callbacks = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancellationError("execution cancelled")

    def on_cancel(self, callback) -> None:
        with self._lock:
            if self.cancelled:
                callback()
            else:
                self._callbacks.append(callback)


class CancellationSource:
    def __init__(self):
        self.token = CancellationToken()

    def cancel(self) -> bool:
        with self.token._lock:
            if self.token.cancelled:
                return False
            self.token._event.set()
            callbacks = tuple(self.token._callbacks)
            self.token._callbacks.clear()
        for callback in callbacks:
            callback()
        return True
