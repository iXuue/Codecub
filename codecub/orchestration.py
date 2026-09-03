"""Role policies and bounded orchestration over the existing Pico Runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import uuid4

from .instructions import Instruction, InstructionLayer

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

ROLE_INSTRUCTIONS = {
    "research": Instruction(
        "Research agent is read-only: inspect the workspace and report evidence; do not modify files.",
        source="agent",
        layer=InstructionLayer.AGENT,
        scope="agent-role",
        scope_id="research",
    ),
    "implement": Instruction(
        "Implement agent may make the smallest justified workspace change and must verify it.",
        source="agent",
        layer=InstructionLayer.AGENT,
        scope="agent-role",
        scope_id="implement",
    ),
    "review": Instruction(
        "Review agent is read-only: inspect the proposed change and report findings without modifying files.",
        source="agent",
        layer=InstructionLayer.AGENT,
        scope="agent-role",
        scope_id="review",
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
    def __init__(self, parent=None, *, model_client=None, model_gateway=None,
                 workspace=None, session_store=None, run_store=None,
                 approval_policy="ask", max_new_tokens=512,
                 secret_env_names=(), shell_env_allowlist=(), event_bus=None,
                 state_ref=None, cancel_checker_ref=None):
        """Create an orchestration port from explicit child-agent settings.

        ``parent`` remains a source-compatible convenience for callers that
        construct an Orchestrator directly.  Values are copied immediately;
        the production Pico composition root uses the explicit form so the
        tool path does not retain the whole Runtime.
        """
        if parent is not None:
            model_client = parent.model_client
            model_gateway = parent.model_gateway
            workspace = parent.workspace
            session_store = parent.session_store
            run_store = parent.run_store
            approval_policy = parent.approval_policy
            max_new_tokens = parent.max_new_tokens
            secret_env_names = parent.secret_env_names
            shell_env_allowlist = parent.shell_env_allowlist
            event_bus = parent.event_bus
            state_ref = getattr(parent, "_tool_state_ref", {"current": getattr(parent, "current_task_state", None)})
            cancel_checker_ref = getattr(parent, "_cancel_checker_ref", {"value": getattr(parent, "cancel_checker", None)})
        self._model_client = model_client
        self._model_gateway = model_gateway
        self._workspace = workspace
        self._session_store = session_store
        self._run_store = run_store
        self._approval_policy = approval_policy
        self._max_new_tokens = max_new_tokens
        self._secret_env_names = secret_env_names
        self._shell_env_allowlist = shell_env_allowlist
        self._event_bus = event_bus
        self._state_ref = state_ref or {"current": None}
        self._cancel_checker_ref = cancel_checker_ref or {"value": None}

    def dispatch(self, role, task, max_steps=4):
        if role not in ROLE_TOOLS:
            raise ValueError("unknown role")
        from .runtime import Pico

        agent_id = uuid4().hex
        parent_run = getattr(self._state_ref.get("current"), "run_id", "")
        self._event_bus.emit(
            "agent.dispatched",
            run_id=parent_run,
            agent_id=agent_id,
            payload={"role": role, "max_steps": max_steps},
        )
        read_only = role in {"research", "review"}
        child = Pico(
            model_client=self._model_client,
            model_gateway=self._model_gateway,
            workspace=self._workspace,
            session_store=self._session_store,
            run_store=self._run_store,
            approval_policy=self._approval_policy,
            max_steps=max_steps,
            max_new_tokens=self._max_new_tokens,
            # A subagent is a leaf: it must never recursively dispatch more
            # agents, while retaining a completely separate session/history.
            depth=0,
            max_depth=0,
            read_only=read_only,
            allowed_tools=ROLE_TOOLS[role],
            secret_env_names=self._secret_env_names,
            shell_env_allowlist=self._shell_env_allowlist,
            agent_role=role,
            agent_instructions=(ROLE_INSTRUCTIONS[role],),
        )
        parent_cancel_checker = self._cancel_checker_ref.get("value")
        if parent_cancel_checker is not None:
            child.cancel_checker = lambda _runtime, _task_state: bool(
                parent_cancel_checker(self, _task_state)
            )
        try:
            self._event_bus.emit(
                "agent.started", run_id=parent_run, agent_id=agent_id, payload={"role": role}
            )
            answer = child.ask(task)
            status = (
                "cancelled"
                if getattr(child.current_task_state, "stop_reason", "") == "user_canceled"
                else "completed"
            )
        except Exception as exc:
            answer = str(exc)
            status = "failed"
        state = child.current_task_state
        self._event_bus.emit(
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
