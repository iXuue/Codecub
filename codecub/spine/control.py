from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

CONTROL_TYPES = frozenset({"RUN_INJECT", "RUN_INTERRUPT", "RUN_CANCEL", "APPROVAL_RESOLVE"})


@dataclass(frozen=True)
class ControlMessage:
    type: str
    run_id: str
    turn_id: str = ""
    session_id: str = ""
    conversation_id: str = ""
    trace_id: str = ""
    payload: dict = field(default_factory=dict)
    control_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.type not in CONTROL_TYPES:
            raise ValueError(f"unsupported control type: {self.type}")
        if not self.run_id:
            raise ValueError("control run_id must not be empty")


class InMemoryControlBus:
    """Contract-test implementation; Redis adapters may redeliver safely."""

    def __init__(self):
        self._lock = Lock()
        self._handled = set()

    def apply_once(self, message: ControlMessage, handler) -> bool:
        with self._lock:
            if message.control_id in self._handled:
                return False
            self._handled.add(message.control_id)
        handler(message)
        return True


class RedisStreamControlBus:
    """Broadcast controls to worker-specific groups with local idempotency."""

    def __init__(self, redis_client, stream="codecub:controls", processed_prefix="codecub:control:", group="", consumer="worker-1"):
        self.redis = redis_client
        self.stream = stream
        self.consumer = consumer
        # Controls are addressed by run_id, not selected by a queue worker.
        # A shared consumer group would let an unrelated worker ACK a control
        # before the worker owning that run observes it.
        self.group = group or f"codecub-controls:{consumer}"
        self.processed_prefix = f"{processed_prefix}{consumer}:"
        try:
            self.redis.xgroup_create(stream, self.group, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, message: ControlMessage):
        return self.redis.xadd(self.stream, {"control": json.dumps(message.__dict__)})

    def apply_once(self, message: ControlMessage, handler) -> bool:
        if not self.redis.set(f"{self.processed_prefix}{message.control_id}", "1", nx=True):
            return False
        handler(message)
        return True

    def consume_one(self):
        response = self.redis.xreadgroup(self.group, self.consumer, {self.stream: ">"}, count=1, block=1)
        if not response:
            return None
        _stream, messages = response[0]
        message_id, fields = messages[0]
        payload = json.loads(fields["control"])
        return message_id, ControlMessage(**payload)

    def ack(self, message_id):
        return self.redis.xack(self.stream, self.group, message_id)
