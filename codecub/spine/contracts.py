"""Stable, surface-neutral contracts for a submitted agent turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Origin(StrEnum):
    USER = "USER"
    CRON = "CRON"
    SUBAGENT = "SUBAGENT"


class BusyPolicy(StrEnum):
    APPEND = "APPEND"
    INJECT = "INJECT"
    INTERRUPT = "INTERRUPT"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def terminal(self) -> bool:
        return self in {self.CANCELLED, self.COMPLETED, self.FAILED}


@dataclass(frozen=True)
class Source:
    channel: str = ""
    chat_id: str = ""
    sender_id: str = ""
    chat_type: str = ""
    message_id: str = ""
    thread_id: str = ""
    extras: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Session:
    session_id: str
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    session_id: str
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class Turn:
    turn_id: str
    conversation_id: str
    session_id: str
    trace_id: str
    created_at: str = field(default_factory=utc_now)


@dataclass
class Run:
    run_id: str
    turn_id: str
    conversation_id: str
    session_id: str
    trace_id: str
    status: RunStatus = RunStatus.QUEUED
    created_at: str = field(default_factory=utc_now)

    def transition_to(self, status: RunStatus) -> None:
        if self.status.terminal:
            raise ValueError("terminal runs cannot transition")
        allowed = {
            RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
            RunStatus.RUNNING: {RunStatus.CANCEL_REQUESTED, RunStatus.COMPLETED, RunStatus.FAILED},
            RunStatus.CANCEL_REQUESTED: {RunStatus.CANCELLING},
            RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
        }
        if status not in allowed.get(self.status, set()):
            raise ValueError(f"invalid run transition: {self.status} -> {status}")
        self.status = status


@dataclass(frozen=True)
class TurnRequest:
    message: str
    session_id: str
    conversation_id: str
    origin: Origin = Origin.USER
    source: Source = field(default_factory=Source)
    busy_policy: BusyPolicy = BusyPolicy.APPEND
    turn_id: str = field(default_factory=lambda: new_id("turn"))
    trace_id: str = field(default_factory=lambda: new_id("trace"))
    workspace: str = ""
    submitted_at: str = field(default_factory=utc_now)
    runtime_extensions: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("TurnRequest.message must not be empty")


@dataclass(frozen=True)
class TurnOutcome:
    turn_id: str
    run_id: str
    status: RunStatus
    answer: str = ""
    error: str = ""
