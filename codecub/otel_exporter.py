"""Optional, redacted OpenTelemetry adapter for runtime events."""

from __future__ import annotations


SAFE_ATTRIBUTE_KEYS = {
    "run_id", "agent_id", "agent_role", "model", "provider", "tool",
    "tool_status", "retrieval_strategy", "cache_hit", "fallback_used",
    "input_tokens", "output_tokens", "duration_ms",
}


class OpenTelemetryEventExporter:
    def __init__(self, tracer):
        self.tracer = tracer

    @classmethod
    def from_environment(cls, service_name="codecub"):
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError as exc:
            raise RuntimeError(
                "OTEL support requires `pip install codecub[otel]`"
            ) from exc
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        return cls(trace.get_tracer("codecub"))

    def publish(self, event):
        attributes = {
            key: value
            for key, value in event.payload.items()
            if key in SAFE_ATTRIBUTE_KEYS and isinstance(value, (str, int, float, bool))
        }
        attributes.update({"run_id": event.run_id, "agent_id": event.agent_id})
        with self.tracer.start_as_current_span(event.event_type) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        return event
