"""Bounded, serial per-channel delivery for app and integration events."""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


class DeliveryError(RuntimeError):
    """Raised after an outlet has exhausted its delivery retries."""


class DeliveryBackpressure(DeliveryError):
    """Raised when a channel queue is full and cannot accept a message."""


class DeliveryOutlet(Protocol):
    def deliver(self, message: "DeliveryMessage") -> Any: ...


@dataclass(frozen=True)
class DeliveryMessage:
    channel: str
    event_type: str
    session_id: str = ""
    run_id: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = False

    def __post_init__(self):
        if not str(self.channel).strip():
            raise ValueError("delivery channel must not be empty")
        if not str(self.event_type).strip():
            raise ValueError("delivery event type must not be empty")


@dataclass(frozen=True)
class DeliveryResult:
    message: DeliveryMessage
    attempts: int
    duration_ms: float


class CallbackOutlet:
    """Adapt a callback into the narrow outlet contract."""

    def __init__(self, callback: Callable[[DeliveryMessage], Any]):
        self.callback = callback

    def deliver(self, message: DeliveryMessage):
        return self.callback(message)


@dataclass
class _Pending:
    message: DeliveryMessage
    future: Future


class _Channel:
    def __init__(self, name: str, outlet: DeliveryOutlet, max_queue_size: int):
        self.name = name
        self.outlet = outlet
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self.stop = object()
        self.stats = {
            "published": 0,
            "delivered": 0,
            "retried": 0,
            "failed": 0,
            "rejected": 0,
        }
        self.worker = threading.Thread(
            target=self._run,
            name=f"codecub-delivery-{name}",
            daemon=True,
        )
        self.worker.start()

    def _run(self):
        while True:
            pending = self.queue.get()
            try:
                if pending is self.stop:
                    return
                self._deliver(pending)
            finally:
                self.queue.task_done()

    def _deliver(self, pending: _Pending):
        started = time.perf_counter_ns()
        attempts = 0
        while True:
            attempts += 1
            try:
                self.outlet.deliver(pending.message)
            except Exception as exc:
                if attempts <= self._hub.max_retries:
                    self.stats["retried"] += 1
                    delay = self._hub.retry_base_seconds * (2 ** (attempts - 1))
                    if delay:
                        time.sleep(delay)
                    continue
                self.stats["failed"] += 1
                pending.future.set_exception(
                    DeliveryError(
                        f"delivery failed on channel {self.name!r} after {attempts} attempts: {exc}"
                    )
                )
                return
            self.stats["delivered"] += 1
            pending.future.set_result(
                DeliveryResult(
                    pending.message,
                    attempts,
                    (time.perf_counter_ns() - started) / 1_000_000,
                )
            )
            return


class DeliveryHub:
    """Own bounded queues and serial workers for registered channels."""

    def __init__(
        self,
        outlets: Mapping[str, DeliveryOutlet] | None = None,
        *,
        max_queue_size: int = 128,
        max_retries: int = 2,
        retry_base_seconds: float = 0.05,
    ):
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must be non-negative")
        self.max_retries = int(max_retries)
        self.retry_base_seconds = float(retry_base_seconds)
        self._lock = threading.RLock()
        self._closed = False
        self._max_queue_size = int(max_queue_size)
        self._channels: dict[str, _Channel] = {}
        for name, outlet in (outlets or {}).items():
            self.register(name, outlet)
        for channel in self._channels.values():
            channel._hub = self

    def register(self, name: str, outlet: DeliveryOutlet, replace: bool = False):
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("delivery channel must not be empty")
        with self._lock:
            if self._closed:
                raise DeliveryError("delivery hub is closed")
            if normalized in self._channels and not replace:
                raise ValueError(f"delivery channel already registered: {normalized}")
            if normalized in self._channels:
                previous = self._channels[normalized]
                try:
                    previous.queue.put(previous.stop, timeout=1)
                except queue.Full as exc:
                    raise DeliveryBackpressure(
                        f"cannot replace busy delivery channel: {normalized}"
                    ) from exc
                previous.worker.join(timeout=1)
            channel = _Channel(normalized, outlet, self._queue_size())
            channel._hub = self
            self._channels[normalized] = channel
        return self

    def _queue_size(self) -> int:
        if self._channels:
            return next(iter(self._channels.values())).queue.maxsize
        return getattr(self, "_max_queue_size", 128)

    def publish(self, message: DeliveryMessage, *, wait: bool = True):
        if not isinstance(message, DeliveryMessage):
            raise TypeError("publish expects DeliveryMessage")
        with self._lock:
            if self._closed:
                raise DeliveryError("delivery hub is closed")
            channel = self._channels.get(message.channel)
            if channel is None:
                raise KeyError(f"unknown delivery channel: {message.channel}")
            pending = _Pending(message, Future())
            try:
                channel.queue.put_nowait(pending)
            except queue.Full as exc:
                channel.stats["rejected"] += 1
                raise DeliveryBackpressure(
                    f"delivery channel queue is full: {message.channel}"
                ) from exc
            channel.stats["published"] += 1
        if not wait:
            return pending.future
        return pending.future.result()

    def stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                name: dict(channel.stats)
                for name, channel in self._channels.items()
            }

    def close(self, grace_seconds: float = 2.0):
        """Stop accepting work, drain accepted messages, and stop workers."""

        if grace_seconds < 0:
            raise ValueError("grace_seconds must be non-negative")
        with self._lock:
            if self._closed:
                return
            self._closed = True
            channels = list(self._channels.values())
        deadline = time.monotonic() + grace_seconds
        for channel in channels:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                channel.queue.put(channel.stop, timeout=remaining)
            except queue.Full:
                channel.stats["failed"] += 1
        for channel in channels:
            remaining = max(0.0, deadline - time.monotonic())
            channel.worker.join(timeout=remaining)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()
