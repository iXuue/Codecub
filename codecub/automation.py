"""Persistent local automation that submits turns through the Spine boundary.

The scheduler owns timing and durable job state only.  It never calls a model
or an agent directly: the injected ``submit`` callback must accept a
``TurnRequest`` and is responsible for routing the turn through Spine.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .spine import BusyPolicy, Origin, Source, TurnRequest


class CronScheduleError(ValueError):
    """A persisted automation job has an invalid schedule."""


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise CronScheduleError("at schedule requires an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CronScheduleError("at schedule must be an ISO timestamp") from exc
    return _utc(parsed)


def _parse_every(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(s|m|h|d)?\s*", str(value or ""), re.I)
        if not match:
            raise CronScheduleError("every schedule must be seconds or a duration such as 5m")
        seconds = float(match.group(1)) * {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            None: 1,
        }[match.group(2).lower() if match.group(2) else None]
    if seconds <= 0:
        raise CronScheduleError("every schedule must be positive")
    return seconds


def _cron_field(value: str, minimum: int, maximum: int) -> set[int]:
    result: set[int] = set()
    for atom in str(value).split(","):
        atom = atom.strip()
        if not atom:
            raise CronScheduleError("cron schedule contains an empty field")
        base, _, step_text = atom.partition("/")
        try:
            step = int(step_text) if step_text else 1
        except ValueError as exc:
            raise CronScheduleError("cron step must be an integer") from exc
        if step <= 0:
            raise CronScheduleError("cron step must be positive")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            left, right = base.split("-", 1)
            try:
                start, end = int(left), int(right)
            except ValueError as exc:
                raise CronScheduleError("cron range must be numeric") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise CronScheduleError("cron field must be numeric, '*', range, or list") from exc
        if start < minimum or end > maximum or start > end:
            raise CronScheduleError(f"cron field must be in [{minimum}, {maximum}]")
        result.update(range(start, end + 1, step))
    return result


def _parse_cron(expression: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    fields = str(expression or "").split()
    if len(fields) != 5:
        raise CronScheduleError("cron schedule must contain five fields")
    return (
        _cron_field(fields[0], 0, 59),
        _cron_field(fields[1], 0, 23),
        _cron_field(fields[2], 1, 31),
        _cron_field(fields[3], 1, 12),
        _cron_field(fields[4], 0, 6),
    )


def cron_next(expression: str, after: datetime) -> datetime:
    """Return the next UTC minute matching a five-field cron expression."""
    minute, hour, day, month, weekday = _parse_cron(expression)
    candidate = _utc(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (
            candidate.minute in minute
            and candidate.hour in hour
            and candidate.day in day
            and candidate.month in month
            and candidate.weekday() in weekday
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise CronScheduleError("cron schedule has no match within one year")


@dataclass
class CronJob:
    job_id: str
    session_id: str
    message: str
    schedule_type: str
    schedule: str | float
    conversation_id: str = ""
    enabled: bool = True
    next_run_at: str = ""
    last_submitted_at: str = ""
    last_error: str = ""
    status: str = "scheduled"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, now: datetime | None = None) -> "CronJob":
        if not isinstance(raw, Mapping):
            raise CronScheduleError("cron job must be an object")
        job_id = str(raw.get("id") or raw.get("job_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", job_id):
            raise CronScheduleError("cron job id is invalid")
        session_id = str(raw.get("session_id") or "").strip()
        message = str(raw.get("message") or "").strip()
        if not session_id or not message:
            raise CronScheduleError("cron job requires session_id and message")
        kind = str(raw.get("schedule_type") or raw.get("type") or "").strip().lower()
        if not kind:
            kind = "at" if raw.get("at") is not None else "cron" if raw.get("cron") is not None else "every"
        if kind not in {"at", "every", "cron"}:
            raise CronScheduleError("cron schedule type must be at, every, or cron")
        raw_schedule = raw.get(kind)
        if raw_schedule is None:
            raw_schedule = raw.get("schedule")
        if kind == "at":
            schedule: str | float = _parse_datetime(raw_schedule).isoformat()
        elif kind == "every":
            schedule = _parse_every(raw_schedule)
        else:
            schedule = str(raw_schedule or "").strip()
            _parse_cron(schedule)
        current = _utc(now)
        next_run = str(raw.get("next_run_at") or "").strip()
        if next_run:
            next_run = _parse_datetime(next_run).isoformat()
        elif kind == "at":
            next_run = str(schedule)
        elif kind == "every":
            next_run = (current + timedelta(seconds=float(schedule))).isoformat()
        else:
            next_run = cron_next(str(schedule), current).isoformat()
        return cls(
            job_id=job_id,
            session_id=session_id,
            message=message,
            schedule_type=kind,
            schedule=schedule,
            conversation_id=str(raw.get("conversation_id") or session_id),
            enabled=bool(raw.get("enabled", True)),
            next_run_at=next_run,
            last_submitted_at=str(raw.get("last_submitted_at") or ""),
            last_error=str(raw.get("last_error") or ""),
            status=str(raw.get("status") or "scheduled"),
            metadata=dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), Mapping) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.job_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "message": self.message,
            "schedule_type": self.schedule_type,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "next_run_at": self.next_run_at,
            "last_submitted_at": self.last_submitted_at,
            "last_error": self.last_error,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


class CronStore:
    """Atomic JSON persistence for local automation jobs."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, CronJob]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CronScheduleError("cron store is not valid JSON") from exc
            values = payload.get("jobs", []) if isinstance(payload, dict) else []
            jobs = {}
            for raw in values:
                job = CronJob.from_mapping(raw)
                jobs[job.job_id] = job
            return jobs

    def save(self, jobs: Mapping[str, CronJob]) -> None:
        payload = {"version": 1, "jobs": [job.to_dict() for job in jobs.values()]}
        with self._lock:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, dir=str(self.path.parent),
                prefix=self.path.name + ".", suffix=".tmp",
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                temporary = handle.name
            Path(temporary).replace(self.path)


SubmitTurn = Callable[[TurnRequest], Any]


class AutomationScheduler:
    """Persistent cron/every/at service with an injectable Spine submitter."""

    def __init__(
        self,
        store: CronStore,
        submit: SubmitTurn,
        *,
        tick_interval_seconds: float = 1.0,
        clock: Callable[[], datetime] | None = None,
    ):
        if tick_interval_seconds <= 0:
            raise ValueError("tick interval must be positive")
        self.store = store
        self.submit = submit
        self.tick_interval_seconds = float(tick_interval_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._jobs = store.load()
        self._lock = threading.RLock()
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[Any] | None = None

    def create(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        job = CronJob.from_mapping(raw, now=_utc(self.clock()))
        with self._lock:
            if job.job_id in self._jobs:
                raise CronScheduleError(f"cron job already exists: {job.job_id}")
            self._jobs[job.job_id] = job
            self.store.save(self._jobs)
        return job.to_dict()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.to_dict() for job in self._jobs.values()]

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            if job is None:
                raise KeyError(job_id)
            job.enabled = False
            job.status = "cancelled"
            self.store.save(self._jobs)
            return job.to_dict()

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = _utc(now or self.clock())
        due: list[CronJob] = []
        with self._lock:
            for job in self._jobs.values():
                if not job.enabled or not job.next_run_at:
                    continue
                if _parse_datetime(job.next_run_at) <= current:
                    due.append(job)
            for job in due:
                submitted_at = current.isoformat()
                job.last_submitted_at = submitted_at
                job.last_error = ""
                if job.schedule_type == "at":
                    job.enabled = False
                    job.status = "completed"
                    job.next_run_at = ""
                elif job.schedule_type == "every":
                    job.next_run_at = (current + timedelta(seconds=float(job.schedule))).isoformat()
                    job.status = "scheduled"
                else:
                    job.next_run_at = cron_next(str(job.schedule), current).isoformat()
                    job.status = "scheduled"
            if due:
                self.store.save(self._jobs)
        submitted: list[dict[str, Any]] = []
        for job in due:
            request = TurnRequest(
                message=job.message,
                session_id=job.session_id,
                conversation_id=job.conversation_id or job.session_id,
                origin=Origin.CRON,
                source=Source(channel="cron", chat_id=job.session_id, extras={"job_id": job.job_id}),
                busy_policy=BusyPolicy.APPEND,
                runtime_extensions={"automation_job_id": job.job_id},
            )
            try:
                self.submit(request)
                submitted.append({"job_id": job.job_id, "submitted_at": current.isoformat()})
            except Exception as exc:
                with self._lock:
                    job.last_error = str(exc)
                    job.status = "submit_failed"
                    if job.schedule_type == "at":
                        job.enabled = True
                        job.next_run_at = (current + timedelta(seconds=self.tick_interval_seconds)).isoformat()
                    self.store.save(self._jobs)
        return submitted

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        await asyncio.gather(task, return_exceptions=True)
        self._stop_event = None

    async def _run(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.tick_interval_seconds)
            except asyncio.TimeoutError:
                continue

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "jobs": self.list(),
                "running": self._task is not None and not self._task.done(),
                "tick_interval_seconds": self.tick_interval_seconds,
            }


__all__ = [
    "AutomationScheduler",
    "CronJob",
    "CronScheduleError",
    "CronStore",
    "cron_next",
]
