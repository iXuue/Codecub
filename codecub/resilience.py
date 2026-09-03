"""Tool circuit breaker primitives; retries are intentionally left to callers."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float | None = None
    half_open_in_flight: bool = False


class ToolCircuitBreaker:
    def __init__(self, failure_threshold=3, reset_seconds=60.0, clock=time.monotonic):
        self.failure_threshold, self.reset_seconds, self.clock = (
            failure_threshold,
            reset_seconds,
            clock,
        )
        self.states = {}

    def _state(self, name):
        return self.states.setdefault(name, CircuitState())

    def status(self, name):
        state = self._state(name)
        if state.opened_at is not None:
            if self.clock() - state.opened_at >= self.reset_seconds:
                return "half_open"
            return "open"
        return "closed"

    def allow(self, name):
        state = self._state(name)
        status = self.status(name)
        if status == "open" or (status == "half_open" and state.half_open_in_flight):
            return False
        if status == "half_open":
            state.half_open_in_flight = True
        return True

    def record_success(self, name):
        self.states[name] = CircuitState()

    def record_failure(self, name):
        state = self._state(name)
        state.half_open_in_flight = False
        state.failures += 1
        if state.failures >= self.failure_threshold:
            state.opened_at = self.clock()

    def result(self, name, fn, *args, **kwargs):
        if not self.allow(name):
            return {
                "ok": False,
                "error": "circuit_open",
                "circuit_state": self.status(name),
            }
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:
            self.record_failure(name)
            return {"ok": False, "error": str(exc), "circuit_state": self.status(name)}
        self.record_success(name)
        return {"ok": True, "value": value, "circuit_state": "closed"}
