"""Surface-neutral channel contracts backed by the existing DeliveryHub."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .spine import DeliveryHub, DeliveryMessage, Origin, Source, TurnRequest


class ChannelError(RuntimeError):
    """A channel adapter cannot accept or deliver a message."""


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    conversation_id: str
    text: str
    sender_id: str = ""
    message_id: str = ""
    thread_id: str = ""
    received_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.channel).strip() or not str(self.conversation_id).strip():
            raise ValueError("inbound channel and conversation_id are required")
        if not str(self.text).strip():
            raise ValueError("inbound text must not be empty")


@dataclass(frozen=True)
class OutboundMessage:
    channel: str
    conversation_id: str
    text: str
    reply_to: str = ""
    idempotency_key: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.channel).strip() or not str(self.conversation_id).strip():
            raise ValueError("outbound channel and conversation_id are required")
        if not str(self.text).strip():
            raise ValueError("outbound text must not be empty")


class ChannelAdapter(Protocol):
    name: str

    def start(self, on_message: Callable[[InboundMessage], Any]) -> None: ...

    def send(self, message: OutboundMessage) -> Any: ...

    def stop(self) -> None: ...


class _AdapterOutlet:
    def __init__(self, adapter: ChannelAdapter):
        self.adapter = adapter

    def deliver(self, message: DeliveryMessage) -> Any:
        payload = dict(message.payload or {})
        outbound = OutboundMessage(
            channel=message.channel,
            conversation_id=str(payload.get("conversation_id") or message.session_id),
            text=str(payload.get("text") or ""),
            reply_to=str(payload.get("reply_to") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )
        return self.adapter.send(outbound)


class ChannelRegistry:
    """Register adapters, route inbound turns, and deliver bounded outbound work."""

    def __init__(self, *, delivery: DeliveryHub | None = None, submit: Callable[[TurnRequest], Any] | None = None):
        self.delivery = delivery or DeliveryHub()
        self._adapters: dict[str, ChannelAdapter] = {}
        self._started = False
        self._submitter = submit

    def register(self, adapter: ChannelAdapter, *, replace: bool = False):
        name = str(getattr(adapter, "name", "") or "").strip()
        if not name:
            raise ValueError("channel adapter name must not be empty")
        if name in self._adapters and not replace:
            raise ValueError(f"channel adapter already registered: {name}")
        self._adapters[name] = adapter
        self.delivery.register(name, _AdapterOutlet(adapter), replace=replace)
        if self._started:
            adapter.start(self._on_message)
        return self

    def start(self, submit: Callable[[TurnRequest], Any] | None = None) -> None:
        if self._started:
            return
        if submit is not None:
            self._submitter = submit
        self._started = True
        for adapter in self._adapters.values():
            adapter.start(self._on_message)

    def publish(self, message: OutboundMessage, *, wait: bool = True):
        return self.delivery.publish(
            DeliveryMessage(
                channel=message.channel,
                event_type="message.outbound",
                session_id=message.conversation_id,
                payload={
                    "conversation_id": message.conversation_id,
                    "text": message.text,
                    "reply_to": message.reply_to,
                    "idempotency_key": message.idempotency_key,
                    "metadata": dict(message.metadata),
                },
            ),
            wait=wait,
        )

    def ingest(self, message: InboundMessage, submit: Callable[[TurnRequest], Any]):
        if message.channel not in self._adapters:
            raise KeyError(f"unknown channel: {message.channel}")
        request = TurnRequest(
            message=message.text,
            session_id=message.conversation_id,
            conversation_id=message.conversation_id,
            origin=Origin.USER,
            source=Source(
                channel=message.channel,
                chat_id=message.conversation_id,
                sender_id=message.sender_id,
                message_id=message.message_id,
                thread_id=message.thread_id,
                extras={str(key): str(value) for key, value in message.metadata.items()},
            ),
            workspace="",
            runtime_extensions={"channel_message_id": message.message_id},
        )
        return submit(request)

    def snapshot(self) -> dict[str, Any]:
        return {
            "started": self._started,
            "channels": sorted(self._adapters),
            "delivery": self.delivery.stats(),
        }

    def _on_message(self, message: InboundMessage):
        if self._submitter is None:
            return message
        return self.ingest(message, self._submitter)

    def close(self) -> None:
        for adapter in self._adapters.values():
            try:
                adapter.stop()
            except Exception:
                continue
        self.delivery.close()
        self._started = False


class LoopbackChannel:
    """Deterministic local adapter used by tests and desktop integrations."""

    name = "loopback"

    def __init__(self):
        self.received: list[OutboundMessage] = []
        self._on_message: Callable[[InboundMessage], Any] | None = None

    def start(self, on_message: Callable[[InboundMessage], Any]) -> None:
        self._on_message = on_message

    def send(self, message: OutboundMessage) -> None:
        self.received.append(message)

    def inject(self, message: InboundMessage):
        if self._on_message is None:
            raise ChannelError("loopback channel is not started")
        return self._on_message(message)

    def stop(self) -> None:
        self._on_message = None


__all__ = [
    "ChannelAdapter",
    "ChannelError",
    "ChannelRegistry",
    "InboundMessage",
    "LoopbackChannel",
    "OutboundMessage",
]
