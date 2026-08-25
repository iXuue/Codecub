"""Agent-run queues; local is default and Redis is an optional adapter."""

from __future__ import annotations
from collections import deque
import json
from typing import Protocol


class RunQueue(Protocol):
    def enqueue(self, run): ...
    def dequeue(self): ...
    def complete(self, run_id): ...
    def fail(self, run_id, error): ...


class LocalRunQueue:
    def __init__(self):
        self.pending, self.statuses = deque(), {}

    def enqueue(self, run):
        run_id = run["run_id"]
        if self.statuses.get(run_id) in {"queued", "running", "completed"}:
            return False
        self.pending.append(dict(run))
        self.statuses[run_id] = "queued"
        return True

    def dequeue(self):
        if not self.pending:
            return None
        run = self.pending.popleft()
        self.statuses[run["run_id"]] = "running"
        return run

    def complete(self, run_id):
        self.statuses[run_id] = "completed"

    def fail(self, run_id, error):
        self.statuses[run_id] = "failed"
        return {"run_id": run_id, "error": str(error)}


class RedisStreamRunQueue:
    """Optional Redis Streams queue; importing this module never requires redis."""

    def __init__(
        self,
        redis_client,
        stream="codecub:runs",
        dead_letter_stream="codecub:runs:dlq",
        group="codecub-workers",
        consumer="worker-1",
        max_deliveries=3,
    ):
        self.redis, self.stream, self.dead_letter_stream = (
            redis_client,
            stream,
            dead_letter_stream,
        )
        self.group, self.consumer, self.max_deliveries = group, consumer, max_deliveries
        self.inflight, self.deliveries = {}, {}
        try:
            self.redis.xgroup_create(stream, group, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    @classmethod
    def from_url(cls, url, **kwargs):
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("Redis support requires `pip install codecub[redis]`") from exc
        return cls(redis.Redis.from_url(url, decode_responses=True), **kwargs)

    def enqueue(self, run):
        key = f"{self.stream}:run:{run['run_id']}"
        if not self.redis.set(key, "queued", nx=True):
            return False
        self.redis.xadd(self.stream, {"run": json.dumps(run, ensure_ascii=False)})
        return True

    def dequeue(self):
        response = self.redis.xreadgroup(
            self.group, self.consumer, {self.stream: ">"}, count=1, block=1
        )
        if not response:
            return None
        _stream, messages = response[0]
        message_id, fields = messages[0]
        self.deliveries[message_id] = self.deliveries.get(message_id, 0) + 1
        run = json.loads(fields["run"])
        if self.deliveries[message_id] > self.max_deliveries:
            self.redis.xadd(
                self.dead_letter_stream,
                {"run": fields["run"], "reason": "max_deliveries"},
            )
            self.redis.xack(self.stream, self.group, message_id)
            return self.dequeue()
        run["_queue_message_id"] = message_id
        self.inflight[run["run_id"]] = message_id
        return run

    def reclaim_stale_pending(self, min_idle_ms=60_000):
        """Claim one stale pending message for this consumer, if supported."""
        claimed = self.redis.xautoclaim(
            self.stream, self.group, self.consumer, min_idle_ms, "0-0", count=1
        )
        messages = claimed[1] if len(claimed) > 1 else []
        if not messages:
            return None
        message_id, fields = messages[0]
        self.deliveries[message_id] = self.deliveries.get(message_id, 0) + 1
        run = json.loads(fields["run"])
        run["_queue_message_id"] = message_id
        self.inflight[run["run_id"]] = message_id
        return run

    def complete(self, run_id):
        message_id = self.inflight.pop(run_id, None)
        if message_id:
            self.redis.xack(self.stream, self.group, message_id)
        self.redis.set(f"{self.stream}:run:{run_id}", "completed")
        return run_id

    def fail(self, run_id, error):
        # Do not ACK here: the entry remains pending and can be reclaimed for
        # retry.  An operator/worker can dead-letter it after max deliveries.
        self.redis.set(f"{self.stream}:run:{run_id}", f"failed:{error}")
        return {"run_id": run_id, "error": str(error)}
