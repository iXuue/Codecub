"""Synchronous, provider-agnostic resilience wrapper for model clients."""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
from dataclasses import dataclass
from http.client import RemoteDisconnected


@dataclass(frozen=True)
class GatewayPolicy:
    max_concurrency: int = 2
    min_interval_seconds: float = 0.0
    max_retries: int = 2
    retry_base_seconds: float = 1.0


def is_transient_model_error(exc: BaseException) -> bool:
    # HTTPError is a URLError subclass: classify it first so permanent 4xx
    # authentication/request failures cannot consume retry or fallback budget.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    if isinstance(
        exc,
        (
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            RemoteDisconnected,
            urllib.error.URLError,
        ),
    ):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connection reset",
            "remotedisconnected",
            "http 408",
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        )
    )


class ModelGateway:
    """Decorates a ModelClient without changing the synchronous Runtime API."""

    def __init__(
        self,
        primary,
        policy: GatewayPolicy | None = None,
        fallback=None,
        event_sink=None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.policy = policy or GatewayPolicy()
        self.event_sink = event_sink
        self._semaphore = threading.BoundedSemaphore(
            max(1, self.policy.max_concurrency)
        )
        self._interval_lock = threading.Lock()
        self._last_start = 0.0
        self.last_completion_metadata = {}

    def __getattr__(self, name):
        return getattr(self.primary, name)

    def _emit(self, event, payload):
        if self.event_sink:
            self.event_sink(event, payload)

    def _start_request(self):
        began = time.monotonic()
        self._semaphore.acquire()
        with self._interval_lock:
            delay = max(
                0.0,
                self.policy.min_interval_seconds
                - (time.monotonic() - self._last_start),
            )
            if delay:
                time.sleep(delay)
            self._last_start = time.monotonic()
        return int((time.monotonic() - began) * 1000)

    def _invoke(self, method, *args, **kwargs):
        wait_ms = self._start_request()
        attempts, last_error, client = 0, "", self.primary
        try:
            attempt = 0
            while True:
                attempts = attempt + 1
                try:
                    value = getattr(client, method)(*args, **kwargs)
                    self.last_completion_metadata = dict(
                        getattr(client, "last_completion_metadata", {}) or {}
                    )
                    self.last_completion_metadata.update(
                        {
                            "fallback_used": client is self.fallback,
                            "fallback_from": getattr(self.primary, "model", ""),
                            "fallback_to": getattr(client, "model", "")
                            if client is self.fallback
                            else "",
                            "retry_count": attempt
                            if client is self.primary
                            else self.policy.max_retries,
                            "attempts": attempts,
                            "last_transient_error": last_error,
                            "gateway_wait_ms": wait_ms,
                        }
                    )
                    return value
                except Exception as exc:
                    if not is_transient_model_error(exc):
                        raise
                    last_error = str(exc)
                    if attempt < self.policy.max_retries:
                        self._emit(
                            "model.retry", {"attempt": attempts, "error": last_error}
                        )
                        time.sleep(self.policy.retry_base_seconds * (2**attempt))
                        attempt += 1
                        continue
                    if client is self.primary and self.fallback is not None:
                        self._emit(
                            "model.fallback",
                            {
                                "from": getattr(self.primary, "model", ""),
                                "to": getattr(self.fallback, "model", ""),
                                "error": last_error,
                            },
                        )
                        client, attempts, attempt = self.fallback, 0, 0
                        continue
                    raise
        finally:
            self._semaphore.release()

    def complete(self, *args, **kwargs):
        return self._invoke("complete", *args, **kwargs)

    def complete_with_tools(self, *args, **kwargs):
        return self._invoke("complete_with_tools", *args, **kwargs)

    def stream_complete(self, *args, **kwargs):
        return self._invoke("stream_complete", *args, **kwargs)
