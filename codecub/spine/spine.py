from __future__ import annotations

from threading import Lock

from ..tracing import TraceContext, TraceRecorder
from .contracts import BusyPolicy, Origin, RunStatus, TurnRequest
from .lane import ConversationLane
from .execution_broker import EmbeddedExecutionBroker
from .resource_pool import ResourcePools


class Spine:
    """The unified scheduling entry point, retaining the legacy runner seam."""

    def __init__(self, runner=None, pools: ResourcePools | None = None, trace_emit=None, execution_broker=None):
        if runner is None and execution_broker is None:
            raise ValueError("Spine requires a runner or execution_broker")
        self.runner = runner
        self.execution_broker = execution_broker or EmbeddedExecutionBroker(runner)
        self.pools = pools or ResourcePools()
        self.traces = TraceRecorder(trace_emit or (lambda _event: None))
        self._lanes: dict[str, ConversationLane] = {}
        self._lock = Lock()

    def submit(self, request: TurnRequest):
        with self._lock:
            lane = self._lanes.setdefault(
                request.conversation_id,
                ConversationLane(request.conversation_id, self._dispatch, self._record_injection_fallback),
            )
        applied, reason = self._applied_policy(request)
        context = TraceContext(request.trace_id, request.session_id, request.conversation_id, request.turn_id)
        payload = {"origin": request.origin.value, "requested_policy": request.busy_policy.value, "applied_policy": applied.value}
        if reason:
            payload["demotion_reason"] = reason
        self.traces.record("turn.submitted", context, payload)
        if applied is BusyPolicy.INTERRUPT:
            self.traces.record("turn.interrupt_requested", context, payload)
            return lane.interrupt_and_submit(request)
        if applied is BusyPolicy.INJECT and lane.inject(request):
            self.traces.record("turn.injected", context, payload)
            return None
        return lane.submit(request)

    @staticmethod
    def _applied_policy(request: TurnRequest) -> tuple[BusyPolicy, str]:
        if request.origin is not Origin.USER and request.busy_policy in {BusyPolicy.INJECT, BusyPolicy.INTERRUPT}:
            return BusyPolicy.APPEND, "origin_not_authorized"
        return request.busy_policy, ""

    def drain_injections(self, conversation_id: str):
        with self._lock:
            lane = self._lanes.get(conversation_id)
        return lane.drain_injections() if lane else []

    def _dispatch(self, request, run, lane):
        request.runtime_extensions["injection_provider"] = lane.drain_injections
        context = TraceContext(request.trace_id, request.session_id, request.conversation_id, request.turn_id, run.run_id)
        if not getattr(self.execution_broker, "durable", False):
            run.transition_to(RunStatus.RUNNING)
        self.traces.record("run.started", context)
        return self.pools.submit(request.origin, self.execution_broker.submit, request, run)

    def _record_injection_fallback(self, request: TurnRequest) -> None:
        context = TraceContext(request.trace_id, request.session_id, request.conversation_id, request.turn_id)
        self.traces.record(
            "turn.inject_fallback_appended",
            context,
            {"requested_policy": BusyPolicy.INJECT.value, "applied_policy": BusyPolicy.APPEND.value},
        )
