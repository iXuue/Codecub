"""Role policies and bounded orchestration over the existing Pico Runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import uuid4


ROLE_TOOLS = {
    "research": (
        "list_files",
        "read_file",
        "search",
        "retrieve_code",
        "symbol_search",
        "file_outline",
        "find_references",
    ),
    "implement": (
        "list_files",
        "read_file",
        "search",
        "retrieve_code",
        "symbol_search",
        "file_outline",
        "find_references",
        "patch_file",
        "write_file",
        "run_shell",
    ),
    "review": (
        "list_files",
        "read_file",
        "search",
        "retrieve_code",
        "symbol_search",
        "file_outline",
        "find_references",
        "run_shell",
    ),
}


@dataclass(frozen=True)
class AgentResult:
    agent_id: str
    role: str
    status: str
    answer: str
    tool_steps: int
    changed_files: list
    verification: list
    model_calls: int = 0


class Orchestrator:
    def __init__(self, parent):
        self.parent = parent

    def dispatch(self, role, task, max_steps=4):
        if role not in ROLE_TOOLS:
            raise ValueError("unknown role")
        from .runtime import Pico

        agent_id = uuid4().hex
        parent_run = getattr(self.parent.current_task_state, "run_id", "")
        self.parent.event_bus.emit(
            "agent.dispatched",
            run_id=parent_run,
            agent_id=agent_id,
            payload={"role": role, "max_steps": max_steps},
        )
        read_only = role in {"research", "review"}
        child = Pico(
            model_client=self.parent.model_client,
            model_gateway=self.parent.model_gateway,
            workspace=self.parent.workspace,
            session_store=self.parent.session_store,
            run_store=self.parent.run_store,
            approval_policy=self.parent.approval_policy,
            max_steps=max_steps,
            max_new_tokens=self.parent.max_new_tokens,
            # A subagent is a leaf: it must never recursively dispatch more
            # agents, while retaining a completely separate session/history.
            depth=0,
            max_depth=0,
            read_only=read_only,
            allowed_tools=ROLE_TOOLS[role],
            secret_env_names=self.parent.secret_env_names,
            shell_env_allowlist=self.parent.shell_env_allowlist,
        )
        try:
            self.parent.event_bus.emit(
                "agent.started", run_id=parent_run, agent_id=agent_id, payload={"role": role}
            )
            answer = child.ask(task)
            status = "completed"
        except Exception as exc:
            answer = str(exc)
            status = "failed"
        state = child.current_task_state
        self.parent.event_bus.emit(
            f"agent.{status}",
            run_id=parent_run,
            agent_id=agent_id,
            payload={"role": role, "tool_steps": int(getattr(state, "tool_steps", 0))},
        )
        return AgentResult(
            agent_id,
            role,
            status,
            answer,
            int(getattr(state, "tool_steps", 0)),
            list(getattr(child.working_state, "changed_files", [])),
            list(getattr(child.working_state, "verification", [])),
            int(getattr(state, "attempts", 0)),
        )

    def dispatch_many(self, requests):
        if any(role == "implement" for role, _, _ in requests):
            raise ValueError("parallel implementation is disabled")
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(lambda item: self.dispatch(*item), requests))
