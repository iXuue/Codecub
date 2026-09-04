"""Runtime-owned adapter used by the transport-neutral Gateway.

The Gateway must not become a second agent runtime.  This module keeps the
composition boundary explicit: it owns session addressing and translates RPC
commands into the existing ``Spine``/``TurnRunner`` path, while ``Pico`` and
``AgentLoop`` remain the owners of context, tools, memory, and persistence.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .automation import AutomationScheduler, CronStore
from .spine import (
    ApprovalBroker,
    BusyPolicy,
    LegacyTurnRunner,
    Origin,
    ResourcePools,
    Source,
    Spine,
    TurnRequest,
)


class RuntimeGatewayError(RuntimeError):
    """A runtime command cannot be fulfilled by the embedded host."""


class AgentFactory(Protocol):
    def __call__(self, *, session_id: str | None, resume: bool) -> Any: ...


EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class _ManagedSession:
    session_id: str
    agent: Any
    spine: Spine
    interactions: ApprovalBroker = field(default_factory=ApprovalBroker)
    subscribers: dict[str, EventCallback] = field(default_factory=dict)
    runs: dict[str, Future] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    scheduler: AutomationScheduler | None = None


class EmbeddedRuntimeGateway:
    """Expose the existing Runtime through a narrow, synchronous host port.

    One ``Pico`` is created per session.  The existing ``Spine`` serializes the
    session conversation and retains its busy-policy, cancellation, and
    injection semantics.  The adapter never executes tools or builds context
    itself.
    """

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        user_workers: int = 4,
        system_workers: int = 2,
    ) -> None:
        if user_workers <= 0 or system_workers <= 0:
            raise ValueError("runtime worker counts must be greater than zero")
        self._agent_factory = agent_factory
        self._pools = ResourcePools(user_workers=user_workers, system_workers=system_workers)
        self._sessions: dict[str, _ManagedSession] = {}
        self._run_sessions: dict[str, str] = {}
        self._lock = threading.RLock()
        self._closed = False

    def create_session(self, session_id: str = "") -> dict[str, Any]:
        """Create a new local Runtime session and return a public snapshot."""
        requested = str(session_id or "").strip() or None
        with self._lock:
            self._ensure_open()
            if requested and requested in self._sessions:
                raise RuntimeGatewayError(f"session already exists: {requested}")
        agent = self._agent_factory(session_id=requested, resume=False)
        return self._register_agent(agent, requested)

    def resume_session(self, session_id: str) -> dict[str, Any]:
        """Load an existing persisted session through the normal CLI factory."""
        requested = str(session_id or "").strip()
        if not requested:
            raise RuntimeGatewayError("session_id is required")
        with self._lock:
            self._ensure_open()
            if requested in self._sessions:
                return self.session_info(requested)
        agent = self._agent_factory(session_id=requested, resume=True)
        return self._register_agent(agent, requested)

    def _register_agent(self, agent: Any, requested: str | None) -> dict[str, Any]:
        session = getattr(agent, "session", None)
        session_id = str((session or {}).get("id", "")).strip()
        if not session_id:
            raise RuntimeGatewayError("agent factory returned an agent without a session id")
        if requested and session_id != requested:
            raise RuntimeGatewayError(
                f"agent factory returned session {session_id!r}, expected {requested!r}"
            )
        managed = _ManagedSession(
            session_id=session_id,
            agent=agent,
            spine=Spine(
                LegacyTurnRunner(lambda _request: agent),
                pools=self._pools,
            ),
        )
        managed.scheduler = AutomationScheduler(
            CronStore(
                Path(getattr(agent, "root", "."))
                / ".codecub"
                / "automation"
                / "jobs.json"
            ),
            self.submit_request,
        )
        with self._lock:
            self._ensure_open()
            if session_id in self._sessions:
                raise RuntimeGatewayError(f"session already exists: {session_id}")
            self._sessions[session_id] = managed
        agent.event_handler = lambda name, payload, runtime, task_state: self._on_agent_event(
            session_id, name, payload, runtime, task_state
        )
        agent.approval_handler = lambda name, args, runtime: self._request_approval(
            managed, name, args, runtime
        )
        self._start_scheduler_if_possible(managed)
        return self.session_info(session_id)

    def session_info(self, session_id: str) -> dict[str, Any]:
        managed = self._get_session(session_id)
        return {
            "session_id": managed.session_id,
            "workspace": str(getattr(managed.agent, "root", "")),
            "active_runs": managed.spine.active_run_ids(),
            "extension_registry": getattr(
                getattr(managed.agent, "extension_registry", None), "to_dict", lambda: {}
            )(),
            "automation": managed.scheduler.snapshot() if managed.scheduler is not None else {},
        }

    def close_session(self, session_id: str) -> dict[str, Any]:
        managed = self._get_session(session_id)
        self._stop_scheduler_if_possible(managed)
        for run_id in tuple(managed.spine.active_run_ids().values()):
            managed.spine.cancel_run(run_id)
        with self._lock:
            for run_id, owner in tuple(self._run_sessions.items()):
                if owner == managed.session_id:
                    self._run_sessions.pop(run_id, None)
            self._sessions.pop(managed.session_id, None)
        return {"session_id": managed.session_id, "closed": True}

    def start_run(
        self,
        session_id: str,
        message: str,
        *,
        run_id: str = "",
        busy_policy: str = "APPEND",
        identity: Any = None,
    ) -> dict[str, Any]:
        managed = self._get_session(session_id)
        normalized_message = str(message or "").strip()
        if not normalized_message:
            raise RuntimeGatewayError("message must not be empty")
        try:
            policy = BusyPolicy(str(busy_policy or "APPEND").upper())
        except ValueError as exc:
            raise RuntimeGatewayError("busy_policy must be APPEND, INJECT, or INTERRUPT") from exc
        selected_run_id = str(run_id or "run_" + uuid.uuid4().hex)
        with self._lock:
            owner = self._run_sessions.get(selected_run_id)
            if owner is not None:
                raise RuntimeGatewayError(f"run already exists: {selected_run_id}")
        request = TurnRequest(
            message=normalized_message,
            session_id=managed.session_id,
            conversation_id=managed.session_id,
            origin=Origin.USER,
            source=Source(channel="gateway", chat_id=managed.session_id),
            busy_policy=policy,
            workspace=str(getattr(managed.agent, "root", "")),
            runtime_extensions={
                "run_id": selected_run_id,
                **({"identity": identity} if identity is not None else {}),
            },
        )
        return self._submit_request(managed, request, selected_run_id)

    def submit_request(self, request: TurnRequest) -> dict[str, Any]:
        """Submit an already classified turn, including ``Origin.CRON``."""
        managed = self._get_session(request.session_id)
        selected_run_id = str(
            (request.runtime_extensions or {}).get("run_id") or "run_" + uuid.uuid4().hex
        )
        request.runtime_extensions["run_id"] = selected_run_id
        return self._submit_request(managed, request, selected_run_id)

    def _submit_request(
        self, managed: _ManagedSession, request: TurnRequest, selected_run_id: str
    ) -> dict[str, Any]:
        with self._lock:
            owner = self._run_sessions.get(selected_run_id)
            if owner is not None:
                raise RuntimeGatewayError(f"run already exists: {selected_run_id}")
        future = managed.spine.submit(request)
        if future is None:
            active_run_id = managed.spine.active_run_ids().get(managed.session_id, "")
            self._emit(
                managed,
                "run.injected",
                active_run_id,
                {"injected_run_id": selected_run_id, "message": request.message},
            )
            return {
                "session_id": managed.session_id,
                "run_id": active_run_id or selected_run_id,
                "status": "INJECTED",
                "injected": True,
            }
        with self._lock:
            self._run_sessions[selected_run_id] = managed.session_id
            managed.runs[selected_run_id] = future
            managed.statuses[selected_run_id] = "QUEUED"
        future.add_done_callback(
            lambda completed, sid=managed.session_id, rid=selected_run_id: self._complete_run(
                sid, rid, completed
            )
        )
        self._emit(managed, "run.queued", selected_run_id, {"message": request.message})
        return {"session_id": managed.session_id, "run_id": selected_run_id, "status": "QUEUED"}

    def cancel_run(self, run_id: str, session_id: str = "") -> dict[str, Any]:
        selected = str(run_id or "").strip()
        if not selected:
            raise RuntimeGatewayError("run_id is required")
        with self._lock:
            owner = self._run_sessions.get(selected) or str(session_id or "").strip()
        managed = self._get_session(owner)
        accepted = managed.spine.cancel_run(selected)
        if accepted:
            with self._lock:
                managed.statuses[selected] = "CANCEL_REQUESTED"
            self._emit(managed, "run.cancel_requested", selected, {"reason": "remote_request"})
        return {"session_id": managed.session_id, "run_id": selected, "accepted": accepted}

    def inject_run(self, session_id: str, message: str, identity: Any = None) -> dict[str, Any]:
        return self.start_run(
            session_id,
            message,
            busy_policy=BusyPolicy.INJECT.value,
            identity=identity,
        )

    def interrupt_run(
        self, session_id: str, message: str, run_id: str = "", identity: Any = None
    ) -> dict[str, Any]:
        return self.start_run(
            session_id,
            message,
            run_id=run_id,
            busy_policy=BusyPolicy.INTERRUPT.value,
            identity=identity,
        )

    def resolve_interaction(
        self,
        interaction_id: str,
        value: Any,
        *,
        run_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        selected_session = str(session_id or "").strip()
        with self._lock:
            if not selected_session:
                for candidate in self._sessions.values():
                    if interaction_id in candidate.interactions._pending:
                        selected_session = candidate.session_id
                        break
        managed = self._get_session(selected_session)
        accepted = managed.interactions.resolve(interaction_id, value, run_id=run_id)
        return {"interaction_id": interaction_id, "accepted": accepted}

    def subscribe(self, session_id: str, callback: EventCallback) -> Callable[[], None]:
        managed = self._get_session(session_id)
        subscription_id = uuid.uuid4().hex
        with self._lock:
            managed.subscribers[subscription_id] = callback

        def unsubscribe() -> None:
            with self._lock:
                managed.subscribers.pop(subscription_id, None)

        return unsubscribe

    def cron_create(self, session_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        managed = self._get_session(session_id)
        if managed.scheduler is None:
            raise RuntimeGatewayError("automation scheduler is unavailable")
        values = dict(raw or {})
        values.setdefault("session_id", managed.session_id)
        return managed.scheduler.create(values)

    def cron_list(self, session_id: str) -> dict[str, Any]:
        managed = self._get_session(session_id)
        return managed.scheduler.snapshot() if managed.scheduler is not None else {"jobs": []}

    def cron_cancel(self, session_id: str, job_id: str) -> dict[str, Any]:
        managed = self._get_session(session_id)
        if managed.scheduler is None:
            raise RuntimeGatewayError("automation scheduler is unavailable")
        try:
            return managed.scheduler.cancel(job_id)
        except KeyError as exc:
            raise RuntimeGatewayError(f"unknown cron job: {job_id}") from exc

    async def start_automation(self) -> None:
        with self._lock:
            schedulers = tuple(
                managed.scheduler for managed in self._sessions.values() if managed.scheduler is not None
            )
        await asyncio.gather(*(scheduler.start() for scheduler in schedulers))

    async def stop_automation(self) -> None:
        with self._lock:
            schedulers = tuple(
                managed.scheduler for managed in self._sessions.values() if managed.scheduler is not None
            )
        await asyncio.gather(*(scheduler.stop() for scheduler in schedulers))

    def health(self) -> dict[str, Any]:
        with self._lock:
            sessions = tuple(self._sessions.values())
            return {
                "status": "closed" if self._closed else "ok",
                "mode": "embedded_runtime",
                "sessions": len(sessions),
                "active_runs": sum(len(item.spine.active_run_ids()) for item in sessions),
                "worker_pools": {"user": self._pools.user._max_workers, "system": self._pools.system._max_workers},
                "automation_jobs": sum(
                    len(item.scheduler.list()) for item in sessions if item.scheduler is not None
                ),
            }

    def capabilities(self) -> list[str]:
        return [
            "gateway.auth",
            "health",
            "capabilities",
            "session.create",
            "session.resume",
            "session.close",
            "run.start",
            "run.cancel",
            "run.inject",
            "run.interrupt",
            "interaction.resolve",
            "run.subscribe",
            "cron.create",
            "cron.list",
            "cron.cancel",
        ]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = tuple(self._sessions.values())
        for managed in sessions:
            for run_id in tuple(managed.spine.active_run_ids().values()):
                managed.spine.cancel_run(run_id)
        self._pools.shutdown()

    @staticmethod
    def _start_scheduler_if_possible(managed: _ManagedSession) -> None:
        if managed.scheduler is None:
            return
        try:
            asyncio.get_running_loop().create_task(managed.scheduler.start())
        except RuntimeError:
            # Synchronous hosts can drive the scheduler explicitly through
            # ``cron.list``/their own lifecycle; Gateway hosts get auto-start.
            return

    @staticmethod
    def _stop_scheduler_if_possible(managed: _ManagedSession) -> None:
        if managed.scheduler is None:
            return
        try:
            asyncio.get_running_loop().create_task(managed.scheduler.stop())
        except RuntimeError:
            return

    def _request_approval(self, managed: _ManagedSession, name: str, args: Any, runtime: Any) -> bool:
        task_state = getattr(runtime, "current_task_state", None)
        run_id = str(getattr(task_state, "run_id", ""))
        interaction = managed.interactions.request(
            "approval", run_id, {"tool_name": name, "args": dict(args or {})}
        )
        self._emit(
            managed,
            "interaction.requested",
            run_id,
            {
                "interaction_id": interaction.interaction_id,
                "kind": interaction.kind,
                "tool_name": name,
                "args": dict(args or {}),
            },
        )
        try:
            return bool(managed.interactions.wait(interaction, timeout=300))
        except TimeoutError:
            return False

    def _on_agent_event(
        self,
        session_id: str,
        event_name: str,
        payload: dict[str, Any],
        runtime: Any,
        task_state: Any,
    ) -> None:
        managed = self._get_session(session_id, allow_missing=True)
        if managed is None:
            return
        run_id = str(getattr(task_state, "run_id", "") or "")
        event_payload = dict(payload or {})
        redactor = getattr(runtime, "redact_artifact", None)
        if callable(redactor):
            event_payload = redactor(event_payload)
        self._emit(managed, str(event_name), run_id, event_payload)

    def _complete_run(self, session_id: str, run_id: str, future: Future) -> None:
        managed = self._get_session(session_id, allow_missing=True)
        if managed is None:
            return
        try:
            outcome = future.result()
            status = str(getattr(getattr(outcome, "status", None), "value", getattr(outcome, "status", "FAILED")))
            answer = str(getattr(outcome, "answer", "") or "")
            error = str(getattr(outcome, "error", "") or "")
        except Exception as exc:  # pragma: no cover - defensive Future boundary
            status, answer, error = "FAILED", "", str(exc)
        with self._lock:
            managed.statuses[run_id] = status
            managed.runs.pop(run_id, None)
            self._run_sessions.pop(run_id, None)
        event_name = {
            "COMPLETED": "run.completed",
            "CANCELLED": "run.cancelled",
        }.get(status, "run.failed")
        self._emit(
            managed,
            event_name,
            run_id,
            {"status": status, "answer": answer, "error": error},
        )

    def _emit(self, managed: _ManagedSession, event_name: str, run_id: str, payload: dict[str, Any]) -> None:
        event = {
            "event": event_name,
            "session_id": managed.session_id,
            "run_id": str(run_id or ""),
            "payload": dict(payload or {}),
        }
        with self._lock:
            callbacks = tuple(managed.subscribers.values())
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                # A dead transport must not break the Runtime worker.
                continue

    def _get_session(self, session_id: str, *, allow_missing: bool = False) -> _ManagedSession | None:
        selected = str(session_id or "").strip()
        with self._lock:
            managed = self._sessions.get(selected)
        if managed is None and not allow_missing:
            raise RuntimeGatewayError(f"unknown session: {selected}")
        return managed

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeGatewayError("runtime gateway is closed")


__all__ = ["AgentFactory", "EmbeddedRuntimeGateway", "RuntimeGatewayError"]
