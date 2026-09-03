"""Local event fan-out that preserves legacy Runtime trace ownership."""

from __future__ import annotations
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    event_type: str
    timestamp: str
    run_id: str
    agent_id: str
    payload: dict


class EventBus:
    def publish(self, event: AgentEvent):
        raise NotImplementedError


class LocalEventBus(EventBus):
    def __init__(self):
        self.handlers = []

    def subscribe(self, handler):
        self.handlers.append(handler)

    def publish(self, event):
        for handler in tuple(self.handlers):
            handler(event)
        return event

    def emit(self, event_type, run_id="", agent_id="", payload=None):
        return self.publish(
            AgentEvent(
                uuid.uuid4().hex,
                event_type,
                datetime.now(timezone.utc).isoformat(),
                run_id,
                agent_id,
                dict(payload or {}),
            )
        )


class CompositeEventBus(EventBus):
    def __init__(self, *buses):
        self.buses = buses

    def publish(self, event):
        for bus in self.buses:
            bus.publish(event)
        return event


class RedisEventBackplane(EventBus):
    """Optional Pub/Sub fan-out that drops messages originating from self."""

    def __init__(self, redis_client, channel="codecub:events", origin=None, local=None):
        self.redis, self.channel = redis_client, channel
        self.origin = origin or uuid.uuid4().hex
        self.local = local or LocalEventBus()

    def publish(self, event):
        self.local.publish(event)
        self.redis.publish(
            self.channel,
            json.dumps({"origin": self.origin, "event": event.__dict__}, ensure_ascii=False),
        )
        return event

    def consume(self, raw_message):
        envelope = json.loads(raw_message)
        if envelope.get("origin") == self.origin:
            return None
        event = AgentEvent(**envelope["event"])
        self.local.publish(event)
        return event
