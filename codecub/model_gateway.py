"""Synchronous, provider-agnostic resilience wrapper for model clients."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .provider_contract import ErrorClassification, classify_model_error


@dataclass(frozen=True)
class GatewayPolicy:
    max_concurrency: int = 2
    min_interval_seconds: float = 0.0
    max_retries: int = 2
    retry_base_seconds: float = 1.0


def is_transient_model_error(exc: BaseException) -> bool:
    """Backward-compatible transient predicate backed by the provider contract."""

    return classify_model_error(exc).retryable


class ModelGateway:
    """Decorates a ModelClient without changing the synchronous Runtime API."""

    def __init__(
        self,
        primary,
        policy: GatewayPolicy | None = None,
        fallback=None,
        event_sink=None,
        fallbacks=None,
    ):
        self.primary = primary
        fallback_chain = list(fallbacks or ())
        if fallback is not None and (not fallback_chain or fallback_chain[0] is not fallback):
            fallback_chain.insert(0, fallback)
        self.fallbacks = tuple(client for client in fallback_chain if client is not primary)
        self.fallback = self.fallbacks[0] if self.fallbacks else None
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
        attempts, total_attempts, last_error, client = 0, 0, "", self.primary
        client_index = 0
        last_classification: ErrorClassification | None = None
        clients = (self.primary,) + self.fallbacks
        try:
            attempt = 0
            while True:
                attempts = attempt + 1
                total_attempts += 1
                try:
                    value = getattr(client, method)(*args, **kwargs)
                    self.last_completion_metadata = dict(
                        getattr(client, "last_completion_metadata", {}) or {}
                    )
                    self.last_completion_metadata.update(
                        {
                            "fallback_used": client_index > 0,
                            "fallback_from": getattr(self.primary, "model", ""),
                            "fallback_to": getattr(client, "model", "")
                            if client_index > 0
                            else "",
                            "retry_count": attempt,
                            "attempts": attempts,
                            "total_attempts": total_attempts,
                            "last_transient_error": last_error,
                            "last_error_classification": (
                                last_classification.to_dict()
                                if last_classification is not None
                                else None
                            ),
                            "fallback_chain_index": client_index,
                            "gateway_wait_ms": wait_ms,
                        }
                    )
                    return value
                except Exception as exc:
                    classification = classify_model_error(
                        exc, provider=getattr(client, "provider_name", "")
                    )
                    last_classification = classification
                    if not classification.retryable and not classification.fallback_eligible:
                        raise
                    last_error = str(exc)
                    if classification.retryable and attempt < self.policy.max_retries:
                        self._emit(
                            "model.retry",
                            {
                                "attempt": attempts,
                                "error": last_error,
                                "classification": classification.to_dict(),
                            },
                        )
                        time.sleep(self.policy.retry_base_seconds * (2**attempt))
                        attempt += 1
                        continue
                    if (
                        classification.fallback_eligible
                        and client_index + 1 < len(clients)
                    ):
                        next_client = clients[client_index + 1]
                        self._emit(
                            "model.fallback",
                            {
                                "from": getattr(self.primary, "model", ""),
                                "to": getattr(next_client, "model", ""),
                                "error": last_error,
                                "classification": classification.to_dict(),
                                "chain_index": client_index + 1,
                            },
                        )
                        client_index += 1
                        client, attempts, attempt = next_client, 0, 0
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
