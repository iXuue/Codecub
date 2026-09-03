"""Narrow collaborators used by the Phase-6 AgentLoop.

These adapters are explicit compatibility boundaries around the existing
stateful services.  They do not retain the composition root and do not own
turn finalization or scheduling.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
import re
import threading
import textwrap
import uuid
import weakref

from ..context_assembler import ContextAssembler, ContextSources
from ..context_manager import ContextManager
from ..instruction_loader import InstructionLoadResult, InstructionLoader
from ..instructions import Instruction, InstructionLayer, InstructionResolver
from ..models import ToolCall
from ..workspace import MAX_HISTORY, WorkspaceContext, clip, now
from .. import task_policy
from ..telemetry import build_usage_snapshot
from ..watchdog import ProgressWatchdog
from ..edit_decision import EditDecisionWatchdog
from .. import memory as memorylib
from .. import tools as toolkit


_MODEL_CLIENT_LOCKS = weakref.WeakKeyDictionary()
_MODEL_CLIENT_LOCKS_GUARD = threading.Lock()
_NON_WEAKREFABLE_MODEL_CLIENT_LOCK = threading.Lock()


def _model_client_lock(model_client):
    """Return a lock shared by invokers that wrap the same legacy client.

    Legacy providers expose completion metadata through a mutable attribute.
    Keep the provider call and its snapshot atomic until every provider has a
    per-response metadata API.  The fallback covers test doubles and extension
    clients that cannot be weak-referenced.
    """
    with _MODEL_CLIENT_LOCKS_GUARD:
        try:
            lock = _MODEL_CLIENT_LOCKS.get(model_client)
            if lock is None:
                lock = threading.Lock()
                _MODEL_CLIENT_LOCKS[model_client] = lock
            return lock
        except TypeError:
            return _NON_WEAKREFABLE_MODEL_CLIENT_LOCK


@dataclass(frozen=True)
class ModelInvocationResult:
    response: object
    completion_metadata: dict


@dataclass(frozen=True)
class ContextPrefixState:
    """Small value object used when a context collaborator rebuilds its prefix."""

    text: str
    hash: str
    workspace_fingerprint: str
    tool_signature: str
    built_at: str


class LegacyContextAdapter:
    """Build prompt context from explicit state and context-side collaborators.

    This is the compatibility boundary for the two existing context builders.
    It deliberately does not retain a Pico/Runtime reference: the composition
    root supplies the state, policy values, and existing context compiler at
    construction time, while ``LoopState`` supplies per-turn mutable state.
    """

    def __init__(
        self,
        *,
        root,
        workspace,
        prefix_state,
        tools,
        session,
        session_manager,
        memory,
        memory_v2,
        context_compiler,
        loop_state,
        model_client,
        token_counter,
        approval_policy,
        read_only,
        runtime_mode,
        execution_mode,
        effective_step_budget,
        max_steps,
        emergency_cap,
        context_window,
        max_new_tokens,
        safety_margin_tokens,
        requires_workspace_change,
        feature_flags,
        hooks,
        observer,
        tool_validation,
        secret_env_names=(),
        metadata_state=None,
        resume_state=None,
        context_assembler=None,
        context_validator=None,
        instruction_resolver=None,
        instruction_loader=None,
        agent_role="",
        repository_id="",
        user_instructions=(),
        repository_instructions=(),
        agent_instructions=(),
        tool_instructions=(),
    ):
        self._root = root
        self._workspace = workspace
        self._prefix_state = prefix_state
        self._prefix = str(getattr(prefix_state, "text", ""))
        self._tools = tools
        self._session = session
        self._session_manager = session_manager
        self._memory = memory
        self._memory_v2 = memory_v2
        self._context_compiler = context_compiler
        self._loop_state = loop_state
        self._working_state = getattr(loop_state, "working_state", None)
        self._planning = getattr(loop_state, "planning", {})
        self._model_client = model_client
        self._token_counter = token_counter
        self._approval_policy = approval_policy
        self._read_only = bool(read_only)
        self._runtime_mode = runtime_mode
        self._execution_mode = execution_mode
        self._effective_step_budget = effective_step_budget
        self._max_steps = max_steps
        self._emergency_cap = emergency_cap
        self._context_window = context_window
        self._max_new_tokens = max_new_tokens
        self._safety_margin_tokens = safety_margin_tokens
        self._requires_workspace_change = bool(requires_workspace_change)
        self._feature_flags = dict(feature_flags or {})
        self._hooks = hooks
        self._observer = observer
        self._tool_validation = tool_validation
        self._secret_env_names = {str(name).upper() for name in secret_env_names}
        self._metadata_state = metadata_state if metadata_state is not None else {}
        self._resume_state = resume_state if resume_state is not None else {"status": "no-checkpoint"}
        self._current_memory_result = None
        self._memory_retrieval_signature = ""
        self._last_prefix_refresh = {"workspace_changed": False, "prefix_changed": False}
        self._context_manager = ContextManager(self)
        self._context_assembler = context_assembler or ContextAssembler(
            context_compiler=context_compiler,
            legacy_context_manager=self._context_manager,
        )
        self._context_assembler.bind_legacy_context_manager(self._context_manager)
        self._context_validator = context_validator
        self._last_validation_result = None
        self._instruction_resolver = instruction_resolver or InstructionResolver()
        self._instruction_loader = instruction_loader or InstructionLoader(root)
        self._last_instruction_load_result = InstructionLoadResult()
        self._agent_role = str(agent_role or "").strip()
        self._repository_id = str(repository_id or "").strip()
        self._user_instructions = tuple(user_instructions or ())
        self._repository_instructions = tuple(repository_instructions or ())
        self._agent_instructions = tuple(agent_instructions or ())
        self._tool_instructions = tuple(tool_instructions or ())
        self._last_resolved_instructions = None
        self._pending_resolution_signature = None

    @property
    def context_compiler(self):
        return self._context_compiler

    @property
    def context_assembler(self):
        return self._context_assembler

    @property
    def context_validator(self):
        return self._context_validator

    @property
    def last_assembled_context(self):
        return self._context_assembler.last_assembled_context

    @property
    def last_validation_result(self):
        return self._last_validation_result

    @property
    def instruction_resolver(self):
        return self._instruction_resolver

    @property
    def instruction_loader(self):
        return self._instruction_loader

    @property
    def last_instruction_load_result(self):
        return self._last_instruction_load_result

    @property
    def last_resolved_instructions(self):
        return self._last_resolved_instructions

    @property
    def legacy_context_manager(self):
        return self._context_manager

    @property
    def prefix_state(self):
        return self._prefix_state

    @property
    def workspace(self):
        return self._workspace

    @property
    def working_state(self):
        return getattr(self._loop_state, "working_state", self._working_state)

    @property
    def root(self):
        return self._root

    @property
    def prefix(self):
        return self._prefix

    @property
    def model_client(self):
        return self._model_client

    @property
    def token_counter(self):
        return self._token_counter

    @property
    def session(self):
        return self._session

    @property
    def memory(self):
        return self._memory

    @property
    def context_window(self):
        return self._context_window

    @property
    def max_new_tokens(self):
        return self._max_new_tokens

    @property
    def safety_margin_tokens(self):
        return self._safety_margin_tokens

    @property
    def last_prompt_metadata(self):
        return self._metadata_state.get("prompt", {})

    @property
    def last_completion_metadata(self):
        return self._metadata_state.get("completion", {})

    @property
    def last_compiler_compression_count(self):
        return int(self._metadata_state.get("compression_count", 0) or 0)

    @last_compiler_compression_count.setter
    def last_compiler_compression_count(self, value):
        self._metadata_state["compression_count"] = int(value or 0)

    @property
    def memory_retrieval_signature(self):
        return self._memory_retrieval_signature

    def bind_loop_state(self, loop_state, *, working_state=None, planning=None):
        """Refresh the explicit per-turn state port at the runner boundary."""
        self._loop_state = loop_state
        if working_state is not None:
            self._working_state = working_state
        if planning is not None:
            self._planning = planning

    def _set_metadata(self, key, value):
        target = self._metadata_state.setdefault(key, {})
        target.clear()
        target.update(dict(value or {}))

    def set_last_prompt_metadata(self, metadata):
        self._set_metadata("prompt", metadata)

    def set_last_completion_metadata(self, metadata):
        self._set_metadata("completion", metadata)

    def feature_enabled(self, name):
        return bool(self._feature_flags.get(str(name), False))

    @property
    def current_planning(self):
        return getattr(self._loop_state, "planning", self._planning) or {}

    def _effective_message(self, user_message):
        # ContextAssembler owns the single rendering point for constraints.
        # Keep this port as a string-normalization compatibility helper so the
        # same constraints can also be represented as a protected segment.
        return str(user_message)

    def _validation_requirements(self, protected_constraints):
        entries = tuple(self.evidence_ledger_entries())
        load_result = self._last_instruction_load_result
        return {
            "required_sources": (
                ("working_state",) if self._context_compiler is not None else ()
            ),
            "optional_sources": ("memory", "retrieval"),
            "working_state_required": self._context_compiler is not None,
            "protected_constraints": tuple(protected_constraints),
            "freshness_entries": entries,
            "freshness_required": bool(entries),
            "native_seed_required": True,
            "instruction_load": load_result.to_dict(),
            "loaded_repository_instruction_files": tuple(load_result.loaded_files),
        }

    @staticmethod
    def _instruction_value(value, *, source, layer, scope="global"):
        if isinstance(value, Instruction):
            return value
        return Instruction.from_value(
            value,
            default_source=source,
            default_layer=layer,
            default_scope=scope,
        ) if not isinstance(value, Mapping) else Instruction.from_value(
            {
                **dict(value),
                "source": dict(value).get("source", source),
                "layer": dict(value).get("layer", layer),
                "scope": dict(value).get("scope", scope),
            }
        )

    def _resolve_instructions(self, user_message, protected_constraints, *, task_state=None):
        values = []
        load_result = self._instruction_loader.load(
            self._instruction_target_paths()
        )
        self._last_instruction_load_result = load_result
        if task_state is not None:
            self._observer.emit(
                task_state,
                "instruction_files_discovered",
                {
                    "discovered_files": list(load_result.discovered_files),
                    "target_paths": list(load_result.target_paths),
                    "ignored_count": len(load_result.ignored_files),
                    "error_count": len(load_result.load_errors),
                },
            )
            self._observer.emit(
                task_state,
                "instruction_files_loaded",
                {
                    "loaded_files": list(load_result.loaded_files),
                    "instruction_count": load_result.instruction_count,
                    "scope_paths": [
                        str((item.metadata or {}).get("scope_path", ""))
                        for item in load_result.instructions
                    ],
                    "ignored_codes": sorted(
                        {str(item.get("code", "")) for item in load_result.ignored_files}
                    ),
                    "error_codes": sorted(
                        {str(item.get("code", "")) for item in load_result.load_errors}
                    ),
                },
            )
        values.extend(load_result.instructions)
        if self._context_compiler is not None:
            legacy_prefix = self._prefix
            workspace_marker = legacy_prefix.find("Workspace:")
            if workspace_marker >= 0:
                legacy_prefix = legacy_prefix[:workspace_marker].rstrip()
            values.append(
                Instruction(
                    content=legacy_prefix,
                    source="agent",
                    layer=InstructionLayer.AGENT,
                    metadata={"legacy_prefix": True},
                )
            )
        values.extend(
            self._instruction_value(
                item,
                source="runtime_safety",
                layer=InstructionLayer.RUNTIME_SAFETY,
            )
            for item in (
                "Tool safety and read-only policy are enforced by the runtime; do not attempt to bypass them.",
            )
        )
        values.extend(
            self._instruction_value(
                item,
                source="protected_runtime",
                layer=InstructionLayer.PROTECTED_RUNTIME,
                scope="turn",
            )
            for item in protected_constraints
        )
        values.extend(
            self._instruction_value(
                item,
                source="repository",
                layer=InstructionLayer.REPOSITORY,
                scope="repository",
            )
            for item in self._repository_instructions
        )
        values.extend(
            self._instruction_value(
                item,
                source="agent",
                layer=InstructionLayer.AGENT,
                scope="agent-role" if self._agent_role else "global",
            )
            for item in self._agent_instructions
        )
        values.extend(
            self._instruction_value(
                item,
                source="tool",
                layer=InstructionLayer.TOOL,
                scope="tool",
            )
            for item in self._tool_instructions
        )
        values.extend(
            self._instruction_value(
                item,
                source="user",
                layer=InstructionLayer.USER,
                scope="global",
            )
            for item in self._user_instructions
        )
        values.append(
            Instruction(
                content=str(user_message),
                source="task",
                layer=InstructionLayer.TASK,
                scope="turn",
            )
        )
        state_run_id = str(getattr(task_state, "run_id", "") or "")
        resolved = self._instruction_resolver.resolve(
            values,
            agent_role=self._agent_role,
            repository_id=self._repository_id,
            run_id=state_run_id,
        )
        self._last_resolved_instructions = resolved
        if task_state is not None:
            self._observer.emit(
                task_state,
                "instructions_resolved",
                {
                    "input_count": resolved.input_count,
                    "active_count": resolved.active_count,
                    "instruction_count": len(resolved.instructions),
                    "deduplicated_count": resolved.deduplicated_count,
                    "shadowed_count": resolved.shadowed_count,
                    "conflict_count": resolved.conflict_count,
                },
            )
        return resolved

    def _instruction_target_paths(self):
        """Collect explicit path context without asking the loader to inspect Runtime."""

        paths = []
        state = self.working_state
        if state is not None:
            paths.extend(str(path) for path in (state.changed_files or []) if str(path).strip())
            paths.extend(
                str(item.get("path", ""))
                for item in (state.relevant_symbols or [])
                if isinstance(item, Mapping) and str(item.get("path", "")).strip()
            )
            paths.extend(
                str(item.get("path", ""))
                for item in (state.read_ranges or [])
                if isinstance(item, Mapping) and str(item.get("path", "")).strip()
            )
        for entry in self.current_planning.get("evidence_ledger", []):
            if isinstance(entry, Mapping) and str(entry.get("path", "")).strip():
                paths.append(str(entry["path"]))
        for entry in self.current_planning.get("read_range_ledger", []):
            if isinstance(entry, Mapping) and str(entry.get("path", "")).strip():
                paths.append(str(entry["path"]))
        return tuple(dict.fromkeys(paths))

    @staticmethod
    def _resolution_signature(user_message, protected_constraints):
        return (
            str(user_message),
            tuple(str(item) for item in protected_constraints),
        )

    def build(self, user_message, *, task_state, status_callback=None):
        user_message = self._effective_message(user_message)
        protected_constraints = tuple(
            getattr(self._loop_state, "protected_constraints", []) or []
        )
        resolved_instructions = self._resolve_instructions(
            user_message, protected_constraints, task_state=task_state
        )
        self._pending_resolution_signature = self._resolution_signature(
            user_message, protected_constraints
        )
        self._hooks.before_context(self, user_message=user_message, task_state=task_state)
        refresh = self.refresh_prefix()
        if status_callback is not None:
            status_callback("checking_workspace", "Checking repository state", str(self._root))
        if status_callback is not None:
            status_callback("loading_memory", "Loading session memory", "")
        refreshed_resume_state = self.evaluate_resume_state()
        self._resume_state.clear()
        self._resume_state.update(refreshed_resume_state)
        if status_callback is not None:
            status_callback("building_prompt", "Building prompt", "")

        if self._context_compiler is not None:
            if task_state is not None:
                self._observer.emit(task_state, "context_compile_started", {
                    "step": task_state.tool_steps,
                    "history_entries": len(self._session.get("history", [])),
                    "working_state_facts": len(self.working_state.known_facts),
                })
            self.working_state.refresh_fact_freshness(self._root)
            stale_count = len(self.working_state.stale_facts())
            memory_layer, memory_meta = self.memory_layer()
            assembled = self._context_assembler.assemble(
                ContextSources(
                    user_message=user_message,
                    protocol="legacy_text",
                    working_state=self.working_state,
                    history=tuple(self._session.get("history", [])),
                    pinned_extra=self.pinned_extra(user_message),
                    memory_layer=memory_layer,
                    memory_meta=memory_meta,
                    protected_constraints=protected_constraints,
                    resolved_instructions=resolved_instructions,
                    validation_requirements=self._validation_requirements(
                        protected_constraints
                    ),
                )
            )
            prompt, metadata = assembled.prompt, assembled.metadata
            self._add_legacy_metadata_compat(metadata, prompt, user_message)
            if task_state is not None:
                if stale_count:
                    self._observer.emit(task_state, "context_fact_stale", {
                        "stale_fact_count": stale_count, "step": task_state.tool_steps,
                    })
                if metadata.get("should_compress"):
                    self._observer.emit(task_state, "compression_triggered", {
                        "mode": "legacy",
                        "estimated_tokens": metadata.get("candidate_context_tokens"),
                        "usable_input_budget": metadata.get("usable_input_budget"),
                        "compression_count": metadata.get("compression_count"),
                    })
                    self._observer.emit(task_state, "compression_started", {"mode": "legacy"})
                    self._observer.emit(task_state, "history_span_compacted", {
                        "raw_history_tokens": metadata.get("raw_history_tokens"),
                        "compressed_history_tokens": metadata.get("compressed_history_tokens"),
                    })
                if metadata.get("repo_map_selection", {}).get("selected_files"):
                    self._observer.emit(task_state, "repo_map_selected", {
                        "selected_files": metadata["repo_map_selection"]["selected_files"],
                        "estimated_tokens": metadata["repo_map_selection"].get("estimated_tokens"),
                    })
                self._observer.emit(task_state, "context_compile_finished", {
                    "compilation_metadata": metadata,
                })
        else:
            assembled = self._context_assembler.assemble(
                ContextSources(
                    user_message=user_message,
                    protocol="legacy_text",
                    working_state=self.working_state,
                    history=tuple(self._session.get("history", [])),
                    protected_constraints=protected_constraints,
                    resolved_instructions=resolved_instructions,
                    validation_requirements=self._validation_requirements(
                        protected_constraints
                    ),
                )
            )
            prompt, metadata = assembled.prompt, assembled.metadata

        available_prompt_tokens = self._resolve_prompt_budget()
        metadata.update({
            "prefix_chars": len(self._prefix),
            "workspace_chars": len(self._workspace.text()),
            "memory_chars": len(self.memory_text()),
            "history_chars": len(self.history_text()),
            "request_chars": len(user_message),
            "tool_count": len(self._tools),
            "workspace_docs": len(self._workspace.project_docs),
            "recent_commits": len(self._workspace.recent_commits),
            "prefix_hash": self._prefix_state.hash,
            "prompt_cache_key": self._prefix_state.hash,
            "workspace_fingerprint": self._prefix_state.workspace_fingerprint,
            "tool_signature": self._prefix_state.tool_signature,
            "workspace_changed": refresh["workspace_changed"],
            "prefix_changed": refresh["prefix_changed"],
            "prompt_cache_supported": bool(getattr(self._model_client, "supports_prompt_cache", False)),
            "resume_status": self._resume_state.get("status", "no-checkpoint"),
            "stale_summary_invalidations": int(self._resume_state.get("stale_summary_invalidations", 0)),
            "stale_paths": list(self._resume_state.get("stale_paths", [])),
            "runtime_identity_mismatch_fields": list(self._resume_state.get("runtime_identity_mismatch_fields", [])),
            "context_window": self._context_window,
            "max_new_tokens": int(self._max_new_tokens),
            "safety_margin_tokens": self._safety_margin_tokens,
            "available_prompt_tokens": available_prompt_tokens,
            "token_counter_source": getattr(self._token_counter, "source", "unavailable"),
            "token_counter_quality": getattr(self._token_counter, "quality", "unavailable"),
            "instruction_load": self._last_instruction_load_result.to_dict(),
        })
        metadata.update(self.detected_secret_env_summary())
        self._hooks.after_context(
            self, user_message=user_message, prompt=prompt, metadata=metadata, task_state=task_state
        )
        return prompt, metadata

    def _resolve_prompt_budget(self):
        if self._token_counter is None:
            return None
        from ..token_budget import resolve_prompt_budget
        return resolve_prompt_budget(
            self._context_window, self._max_new_tokens, self._safety_margin_tokens
        )

    def initial_native_messages(self, user_message):
        return self._context_assembler.initial_native_messages(user_message)

    def assemble_native(self, user_message, *, working_state, native_messages):
        working_state.refresh_fact_freshness(self._root)
        memory_layer, memory_meta = self.memory_layer()
        protected_constraints = tuple(
            getattr(self._loop_state, "protected_constraints", []) or []
        )
        resolution_signature = self._resolution_signature(
            user_message, protected_constraints
        )
        if self._pending_resolution_signature == resolution_signature:
            resolved_instructions = self._last_resolved_instructions
            self._pending_resolution_signature = None
        else:
            resolved_instructions = self._resolve_instructions(
                user_message, protected_constraints
            )
        assembled = self._context_assembler.assemble(
            ContextSources(
                user_message=user_message,
                protocol="native_tools",
                protected_constraints=protected_constraints,
                resolved_instructions=resolved_instructions,
                working_state=working_state,
                native_messages=native_messages if native_messages is not None else [],
                pinned_extra=self.pinned_extra(user_message),
                memory_layer=memory_layer,
                memory_meta=memory_meta,
                validation_requirements=self._validation_requirements(
                    protected_constraints
                ),
            )
        )
        assembled.metadata["instruction_load"] = self._last_instruction_load_result.to_dict()
        return assembled.messages, assembled.metadata

    def validate_context(self, assembled=None, *, protocol=None, requirements=None):
        """Validate the last provider-bound assembly through the narrow port."""

        if self._context_validator is None:
            return None
        context = assembled or self.last_assembled_context
        if context is None:
            raise ValueError("validate_context requires an assembled context")
        result = self._context_validator.validate(
            context, protocol=protocol, requirements=requirements
        )
        self._last_validation_result = result
        return result

    # Short alias used by the AgentLoop boundary and by focused integrations.
    validate = validate_context

    def compile_native(self, user_message, *, working_state, native_messages):
        """Compatibility alias; final native assembly belongs to the assembler."""

        return self.assemble_native(
            user_message, working_state=working_state, native_messages=native_messages
        )

    def _resolve_prefix_state(self, text, workspace_fingerprint, tool_signature):
        state_type = type(self._prefix_state)
        try:
            return state_type(
                text=text,
                hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                workspace_fingerprint=workspace_fingerprint,
                tool_signature=tool_signature,
                built_at=now(),
            )
        except TypeError:
            return ContextPrefixState(
                text=text,
                hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                workspace_fingerprint=workspace_fingerprint,
                tool_signature=tool_signature,
                built_at=now(),
            )

    def tool_signature(self):
        payload = []
        for name in sorted(self._tools):
            tool = self._tools[name]
            payload.append({
                "name": name,
                "schema": tool["schema"],
                "risky": tool["risky"],
                "description": tool["description"],
            })
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def build_prefix(self):
        tool_lines = []
        for name, tool in self._tools.items():
            fields = ", ".join(f"{key}: {value}" for key, value in tool["schema"].items())
            risk = "approval required" if tool["risky"] else "safe"
            tool_lines.append(f"- {name}({fields}) [{risk}] {tool['description']}")
        xml_tool_rules = ""
        if "write_file" in self._tools:
            xml_tool_rules += (
                "- For write_file calls with multi-line content, prefer XML style:\n"
                '  <tool name="write_file" path="file.py"><content>...</content></tool>\n'
            )
        if "patch_file" in self._tools:
            xml_tool_rules += (
                "- For patch_file calls with multi-line text, use <old_text> and <new_text>:\n"
                '  <tool name="patch_file" path="file.py"><old_text>...</old_text><new_text>...</new_text></tool>'
            )
        examples = "\n".join(
            [toolkit.tool_example(name) for name in self._tools if toolkit.tool_example(name)]
            + ["<final>Done.</final>"]
        )
        task_contract = (
            "- This task requires an actual workspace modification. Analysis, repository inspection, and test execution alone do not complete it.\n"
            "- Once you have identified a plausible minimal fix, use an allowed editing tool to make the smallest justified change, then verify it.\n"
            "- Do not continue broad exploration after you have enough evidence to make a specific edit."
            if self._requires_workspace_change
            else ""
        )
        tool_text = "\n".join(tool_lines)
        text = textwrap.dedent(
            f"""\
            You are CodeCub, a local coding agent working inside a local repository.

            Rules:
            - Use tools instead of guessing about the workspace.
            - Return exactly one <tool>...</tool> or one <final>...</final>.
            - Tool calls must look like:
              <tool>{{"name":"tool_name","args":{{...}}}}</tool>
            {xml_tool_rules}
            - Final answers must look like:
              <final>your answer</final>
            - Never invent tool results.
            - Keep answers concise and concrete.
            - If the user asks you to create or update a specific file and the path is clear, use write_file or patch_file instead of repeatedly listing files.
            - Before writing tests for existing code, read the implementation first.
            - When writing tests, match the current implementation unless the user explicitly asked you to change the code.
            - New files should be complete and runnable, including obvious imports.
            - Do not repeat the same tool call with the same arguments if it did not help. Choose a different tool or return a final answer.
            - Required tool arguments must not be empty. Do not call read_file, write_file, patch_file, run_shell, or delegate with args={{}}.
            {task_contract}

            Tools:
            {tool_text}

            Valid response examples:
            {examples}

            {self._workspace.text()}
            """
        ).strip()
        return text

    def refresh_prefix(self, force=False):
        previous_hash = getattr(self._prefix_state, "hash", None)
        previous_workspace_fingerprint = getattr(self._prefix_state, "workspace_fingerprint", None)
        refreshed_workspace = WorkspaceContext.build(self._root)
        refreshed_fingerprint = refreshed_workspace.fingerprint()
        workspace_changed = force or refreshed_fingerprint != previous_workspace_fingerprint
        if workspace_changed:
            self._workspace = refreshed_workspace
        prefix_state = (
            self._resolve_prefix_state(
                self.build_prefix(), refreshed_fingerprint, self.tool_signature()
            )
            if workspace_changed or force or previous_hash is None
            else self._prefix_state
        )
        prefix_changed = force or previous_hash != prefix_state.hash
        if prefix_changed:
            self._prefix_state = prefix_state
            self._prefix = prefix_state.text
        self._last_prefix_refresh = {
            "workspace_changed": workspace_changed,
            "prefix_changed": prefix_changed,
        }
        return dict(self._last_prefix_refresh)

    def pinned_extra(self, user_message=None):
        extras = {
            # The static prefix rules are resolved as an agent instruction in
            # the production compiler path.  Workspace facts remain Context,
            # not Instruction, and are kept in their own pinned source.
            "pinned:workspace": self._workspace.text(),
            "pinned:safety": f"Approval policy: {self._approval_policy}; read_only: {self._read_only}",
            "pinned:runtime-mode": (
                f"runtime_mode: {self._runtime_mode}; execution_mode: {self._execution_mode}; "
                f"effective_step_budget: {self._effective_step_budget}; emergency_cap: {self._emergency_cap}"
            ),
        }
        ledger = self.evidence_ledger_text()
        if ledger:
            extras["pinned:evidence-ledger"] = ledger
        checkpoint_text = str(self.render_checkpoint_text() or "").strip()
        if checkpoint_text:
            extras["pinned:checkpoint"] = checkpoint_text
        if (
            not self.memory_v2_enabled()
            and self.feature_enabled("memory")
            and self.feature_enabled("relevant_memory")
        ):
            try:
                notes = self.memory.retrieval_candidates(str(user_message or ""), limit=3)
            except Exception:
                notes = []
            if notes:
                extras["pinned:relevant-memory"] = "\n".join(
                    ["Relevant memory:"] + [f"- {note.get('text', '')}" for note in notes]
                )
        return extras

    def checkpoint_state(self):
        return self._session_manager.checkpoint_state(self._session)

    def current_checkpoint(self):
        return self._session_manager.current_checkpoint(self._session)

    def invalidate_stale_memory(self):
        invalidated = self.memory.invalidate_stale_file_summaries()
        self._session["memory"] = self.memory.to_dict()
        return invalidated

    def current_runtime_identity(self):
        return {
            "session_id": self._session.get("id", ""),
            "cwd": str(self._root),
            "model": str(getattr(self._model_client, "model", "")),
            "model_client": self._model_client.__class__.__name__,
            "approval_policy": self._approval_policy,
            "read_only": self._read_only,
            "max_steps": self._max_steps,
            "runtime_mode": self._runtime_mode,
            "execution_mode": self._execution_mode,
            "emergency_cap": int(self._emergency_cap or 0),
            "max_new_tokens": int(self._max_new_tokens),
            "feature_flags": dict(self._feature_flags),
            "workspace_fingerprint": getattr(self._prefix_state, "workspace_fingerprint", self._workspace.fingerprint()),
            "tool_signature": self.tool_signature(),
        }

    def evaluate_resume_state(self):
        invalidated = self.invalidate_stale_memory()
        if self.memory_v2_enabled():
            try:
                self._memory_v2.refresh_freshness()
            except Exception:
                pass
        return self._session_manager.evaluate_resume(
            self._session,
            invalidated=invalidated,
            file_freshness=lambda path: memorylib.file_freshness(path, self._root),
            runtime_identity=self.current_runtime_identity,
            schema_version="phase1-v1",
            statuses={
                "none": "no-checkpoint",
                "schema_mismatch": "schema-mismatch",
                "partial_stale": "partial-stale",
                "workspace_mismatch": "workspace-mismatch",
                "full_valid": "full-valid",
            },
        )

    def render_checkpoint_text(self):
        checkpoint = self.current_checkpoint()
        if not checkpoint:
            return ""
        lines = [
            "Task checkpoint:",
            f"- Resume status: {self._resume_state.get('status', 'no-checkpoint')}",
            f"- Current goal: {checkpoint.get('current_goal', '-') or '-'}",
            f"- Current blocker: {checkpoint.get('current_blocker', '-') or '-'}",
            f"- Next step: {checkpoint.get('next_step', '-') or '-'}",
        ]
        key_files = [
            str(item.get("path", "")).strip()
            for item in checkpoint.get("key_files", [])
            if str(item.get("path", "")).strip()
        ]
        lines.append(f"- Key files: {', '.join(key_files) or '-'}")
        if checkpoint.get("completed"):
            lines.append("- Completed: " + " | ".join(str(item) for item in checkpoint["completed"]))
        if checkpoint.get("excluded"):
            lines.append("- Excluded: " + " | ".join(str(item) for item in checkpoint["excluded"]))
        if self._resume_state.get("stale_paths"):
            lines.append("- Stale paths: " + ", ".join(self._resume_state["stale_paths"]))
        summary = str(checkpoint.get("summary", "")).strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        return "\n".join(lines)

    def memory_v2_enabled(self):
        return bool(self.feature_enabled("memory") and self.feature_enabled("memory_v2"))

    def memory_layer(self):
        if not self.memory_v2_enabled():
            return "", {}
        result = self._current_memory_result
        if result is None or not result.items:
            return "", {}
        return result.render(), {
            "evidence_count": len(result.evidence_items),
            "durable_count": len(result.durable_items),
            "stale_count": result.stale_count,
            "token_budget": result.token_budget,
        }

    def memory_signature(self):
        state = self.working_state or type("State", (), {"blockers": (), "relevant_symbols": (), "changed_files": ()})()
        blockers = "|".join(str(item.get("text", "")) for item in (state.blockers or []))
        symbols = "|".join(
            f"{item.get('path', '')}:{item.get('name', '')}" for item in (state.relevant_symbols or [])
        )
        files = "|".join(str(path) for path in (state.changed_files or []))
        return f"{blockers}||{symbols}||{files}"

    def refresh_memory_retrieval(self, user_message, force=False):
        if not self.memory_v2_enabled():
            self._current_memory_result = None
            return None
        try:
            result = self._memory_v2.retrieve(user_message, self.working_state, force=force)
        except Exception:
            result = None
        self._current_memory_result = result
        self._memory_retrieval_signature = self.memory_signature()
        return result

    def memory_text(self):
        parts = [self.action_readiness_text(), self.evidence_ledger_text(), self.memory.render_memory_text()]
        return "\n".join(part for part in parts if part)

    def action_readiness_text(self):
        if not self._requires_workspace_change:
            return ""
        if self.current_planning.get("action_readiness") != "action_expected":
            return ""
        return (
            "Action readiness: you have inspected relevant source evidence. "
            "If you can identify a specific minimal fix, make the edit before performing more broad exploration."
        )

    def evidence_ledger_entries(self):
        return list(self.current_planning.get("evidence_ledger", []))

    def evidence_ledger_text(self):
        entries = self.evidence_ledger_entries()
        if not entries:
            return ""
        return "\n".join(
            ["Inspected source evidence (current workspace revision):"]
            + [f"- {entry['path']} lines {entry['start']}-{entry['end']} [{entry['marker']}]: {entry['hint']}" for entry in entries]
        )

    def history_text(self):
        history = self._session["history"]
        if not history:
            return "- empty"
        lines = []
        seen_reads = set()
        recent_start = max(0, len(history) - 6)
        for index, item in enumerate(history):
            recent = index >= recent_start
            if item["role"] == "tool" and item["name"] == "read_file" and not recent:
                path = str(item["args"].get("path", ""))
                if path in seen_reads:
                    continue
                seen_reads.add(path)
            if item["role"] == "tool":
                limit = 900 if recent else 180
                lines.append(f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}")
                lines.append(clip(item["content"], limit))
            else:
                lines.append(f"[{item['role']}] {clip(item['content'], 900 if recent else 220)}")
        return clip("\n".join(lines), MAX_HISTORY)

    def _add_legacy_metadata_compat(self, metadata, prompt, user_message):
        unit = "tokens" if self._token_counter is not None else "chars"
        estimated = metadata.get("compiled_context_tokens") or 0
        evidence_entries = []
        for entry in self.evidence_ledger_entries():
            marker = str(entry.get("marker", ""))
            evidence_entries.append({
                "path": str(entry.get("path", "")),
                "start": int(entry.get("start", 1)),
                "end": int(entry.get("end", 200)),
                "freshness": str(entry.get("freshness", "")),
                "last_read_step": entry.get("last_read_step"),
                "visible": bool(marker and marker in prompt),
            })
        metadata.update({
            "prompt_chars": len(prompt),
            "prompt_budget_chars": self._context_manager.total_budget,
            "prompt_over_budget": False,
            "budget_mode": "token" if self._token_counter is not None else "char",
            "budget_unit": unit,
            "estimated_prompt_tokens": estimated,
            "prompt_tokens": estimated,
            "section_order": ["prefix", "memory", "relevant_memory", "history", "current_request"],
            "section_budgets": {section: None for section in ("prefix", "memory", "relevant_memory", "history", "current_request")},
            "sections": {
                "pinned": {"raw_chars": 0, "budget_chars": None, "rendered_chars": metadata.get("pinned_tokens", 0)},
                "working_state": {"raw_chars": 0, "budget_chars": None, "rendered_chars": metadata.get("working_state_tokens", 0)},
                "recent_verbatim": {"raw_chars": 0, "budget_chars": None, "rendered_chars": metadata.get("recent_verbatim_tokens", 0)},
                "compressed_history": {"raw_chars": 0, "budget_chars": None, "rendered_chars": metadata.get("compressed_history_tokens", 0)},
            },
            "inspected_evidence": {
                "entry_count": len(evidence_entries),
                "visible_entry_count": sum(1 for entry in evidence_entries if entry["visible"]),
                "entries": evidence_entries,
            },
            "budget_reductions": [],
            "reduction_order": [],
            "history": {
                "raw_chars": metadata.get("raw_history_tokens", 0),
                "rendered_chars": metadata.get("recent_verbatim_tokens", 0),
                "older_entries_count": 0,
                "collapsed_duplicate_reads": 0,
                "reused_file_summary_count": 0,
                "summarized_tool_count": 0,
            },
            "current_request": {
                "text": str(user_message),
                "raw_chars": len(str(user_message)),
                "rendered_chars": len(str(user_message)),
                "section_chars": len(str(user_message)),
            },
        })
        if self.memory_v2_enabled() and self._current_memory_result is not None:
            result = self._current_memory_result
            rendered_notes = [
                {
                    "text": item.text, "kind": item.kind, "marker": item.marker,
                    "source": item.path or item.topic or "", "reason": item.reason,
                    "score": item.score, "status": item.status,
                }
                for item in result.items
            ]
            metadata["relevant_memory"] = {
                "limit": result.evidence_top_k + result.durable_top_k,
                "selected_count": len(result.items),
                "selected_notes": [item.text for item in result.items],
                "selected_sources": [item.path or item.topic for item in result.items],
                "selected_kinds": [item.kind for item in result.items],
                "selected_reasons": [item.reason for item in result.items],
                "selected_scores": [round(item.score, 1) for item in result.items],
                "selected_matches": [],
                "selected_durable_count": len(result.durable_items),
                "raw_chars": result.total_tokens,
                "rendered_chars": result.total_tokens,
                "rendered_notes": rendered_notes,
                "rendered_count": len(rendered_notes),
                "stale_count": result.stale_count,
                "missing_count": result.missing_count,
            }

    def create_checkpoint(self, task_state, user_message, trigger):
        state = self.checkpoint_state()
        current = self.current_checkpoint()
        checkpoint_id = "ckpt_" + uuid.uuid4().hex[:8]
        key_files = []
        freshness = {}
        for path in self.memory.to_dict()["working"]["recent_files"]:
            file_freshness = memorylib.file_freshness(path, self._root)
            freshness[path] = file_freshness
            key_files.append({"path": path, "freshness": file_freshness})
        if self.memory_v2_enabled():
            ws_paths = list(str(path) for path in (self.working_state.changed_files or []))
            ws_paths.extend(
                str(item.get("path", "")) for item in (self.working_state.relevant_symbols or [])
                if str(item.get("path", ""))
            )
            ws_paths.extend(
                str(record.get("path", "")) for record in self._memory_v2.evidence_store.latest_records()
                if str(record.get("path", ""))
            )
            for path in ws_paths:
                if any(item["path"] == path for item in key_files):
                    continue
                file_freshness = memorylib.file_freshness(path, self._root)
                freshness[path] = file_freshness
                key_files.append({"path": path, "freshness": file_freshness})
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": current.get("checkpoint_id", "") if current else "",
            "schema_version": "phase1-v1",
            "created_at": now(),
            "current_goal": str(user_message),
            "completed": [task_state.final_answer] if task_state.final_answer else [],
            "excluded": [],
            "current_blocker": "" if str(task_state.stop_reason or "") in ("", "final_answer_returned") else str(task_state.stop_reason),
            "next_step": self.infer_next_step(task_state),
            "key_files": key_files,
            "freshness": freshness,
            "summary": f"{trigger}: {clip(str(user_message), 120)}",
            "runtime_identity": self.current_runtime_identity(),
            "side_effect_operations": dict(task_state.side_effect_operations or {}),
        }
        state["items"][checkpoint_id] = checkpoint
        state["current_id"] = checkpoint_id
        task_state.checkpoint_id = checkpoint_id
        self._session["runtime_identity"] = checkpoint["runtime_identity"]
        self._session_manager.save(self._session)
        return checkpoint

    @staticmethod
    def infer_next_step(task_state):
        if task_state.status == "completed":
            return "No next step recorded."
        if task_state.stop_reason == "step_limit_reached":
            return "Resume from the latest checkpoint and continue the task."
        if task_state.last_tool:
            return f"Decide the next action after {task_state.last_tool}."
        return "Continue the task from the latest checkpoint."

    def _record(self, item):
        self._session["history"].append(dict(item))
        return self._session_manager.save(self._session)

    def read_guard_notice(self, args):
        if not self.current_planning or not self.last_prompt_metadata:
            return "", "new"
        path = task_policy.canonical_path((args or {}).get("path"))
        current_freshness = memorylib.file_freshness(path, self._root)
        entries = (self.last_prompt_metadata.get("inspected_evidence") or {}).get("entries", [])
        candidates = [
            entry for entry in entries
            if entry.get("path") == path
            and entry.get("freshness") == current_freshness
            and task_policy.read_overlap_ratio(args, entry) >= 0.8
        ]
        if not candidates:
            return "", "new"
        entry = candidates[-1]
        classification = "avoidable_repeated_read" if any(bool(item.get("visible")) for item in candidates) else "evidence_evicted_reread"
        if classification != "avoidable_repeated_read":
            return "", classification
        key = (entry["path"], entry["freshness"])
        notices = self.current_planning["read_guard_notices"]
        if key in notices:
            return "", classification
        notices.add(key)
        return (
            "Runtime notice: this range substantially overlaps source code already inspected in the current workspace revision. "
            "Use the existing evidence if it is sufficient. If a specific unresolved detail is needed, read a narrower non-overlapping range.",
            classification,
        )

    def record_read_evidence(self, args, result, tool_step):
        path = task_policy.canonical_path((args or {}).get("path"))
        if not path:
            return
        lines = [line.strip() for line in str(result).splitlines() if line.strip()]
        if lines and lines[0].startswith("# "):
            lines = lines[1:]
        hint = re.sub(
            r"(?i)((?:api[_ -]?key|token|secret|password)\s*[:=]\s*[\"']?)[^\s,\"']+",
            r"\1<redacted>",
            " | ".join(lines[:8]),
        )
        hint = clip(self.redact_text(hint), 280)
        freshness = memorylib.file_freshness(path, self._root)
        self._loop_state.record_read_evidence(
            args, result, tool_step, freshness=freshness, hint=hint
        )
        ledger = self.current_planning["evidence_ledger"]
        if len(ledger) > 6:
            del ledger[: len(ledger) - 6]
            self.current_planning["evidence_eviction_count"] += 1

    def invalidate_evidence_for_paths(self, paths):
        return self._loop_state.invalidate_evidence_for_paths(paths)

    def maybe_emit_exploration_warning(self, task_state):
        state = self.current_planning
        if state["warning_sent"] or (state["consecutive_exploration"] < 6 and state["redundant_exploration_steps"] < 2):
            return False
        state["warning_sent"] = True
        state["exploration_warning_count"] += 1
        notice = (
            "Runtime planning notice: substantial repository exploration has occurred without an implementation action. "
            "Reassess whether further exploration is necessary. If evidence is sufficient, make the smallest justified change and verify it."
        )
        self._record({"role": "assistant", "content": notice, "created_at": now()})
        self._observer.emit(task_state, "exploration_warning", {
            "consecutive_exploration": state["consecutive_exploration"],
            "redundant_exploration_steps": state["redundant_exploration_steps"],
        })
        return True

    def maybe_emit_implementation_warning(self, task_state):
        state = self.current_planning
        if (
            not self._requires_workspace_change
            or state["implementation_warning_sent"]
            or state["workspace_change_count"]
            or state["verification_steps"] < 2
        ):
            return False
        state["implementation_warning_sent"] = True
        state["implementation_warning_count"] += 1
        notice = (
            "Runtime planning notice: this task requires a workspace change, but verification commands have run without a successful change. "
            "Use the evidence already gathered to make the smallest justified implementation change; only continue diagnosing if a concrete question remains unresolved."
        )
        self._record({"role": "assistant", "content": notice, "created_at": now()})
        self._observer.emit(task_state, "implementation_warning", {
            "verification_steps": state["verification_steps"],
            "workspace_change_count": state["workspace_change_count"],
        })
        return True

    def detected_secret_env_summary(self):
        names = []
        for name, value in os.environ.items():
            upper = str(name).upper()
            if value and (upper in self._secret_env_names or any(marker in upper for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))):
                names.append(name)
        return {"secret_env_count": len(names), "secret_env_names": sorted(names)}

    def redact_text(self, value):
        text = str(value)
        secrets = [
            value for name, value in os.environ.items()
            if value and (str(name).upper() in self._secret_env_names or any(marker in str(name).upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")))
        ]
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, "<redacted>")
        return text

    def recover_native_text_tool_call(self, content):
        text = str(content or "")
        opening_tags = text.count("<tool")
        closing_tags = text.count("</tool>")
        if opening_tags > 1:
            return None, "multiple_open_tags"
        if closing_tags > 1:
            return None, "multiple_close_tags"
        if opening_tags == 0 and closing_tags == 1:
            return None, "missing_opening_tag"
        if opening_tags == 1 and closing_tags == 0:
            return None, "missing_closing_tag"
        if opening_tags == 0:
            return None, None
        match = re.fullmatch(r"\s*<tool>\s*(?P<body>.*?)\s*</tool>\s*", text, re.S)
        if not match:
            return None, "ambiguous_content"
        try:
            payload = json.loads(match.group("body"))
        except json.JSONDecodeError:
            return None, "malformed_json"
        if not isinstance(payload, dict) or set(payload) != {"name", "args"}:
            return None, "schema_failure"
        name, args = payload.get("name"), payload.get("args")
        if not isinstance(name, str) or not name.strip():
            return None, "schema_failure"
        if not isinstance(args, dict):
            return None, "invalid_args"
        name = name.strip()
        tool = self._tools.get(name)
        if tool is None or set(args) - set(tool.get("schema", {})):
            return None, "unknown_tool" if tool is None else "schema_failure"
        for key, value in args.items():
            type_name = str(tool["schema"][key]).partition("=")[0]
            valid = {
                "str": isinstance(value, str),
                "int": isinstance(value, int) and not isinstance(value, bool),
                "float": isinstance(value, (int, float)) and not isinstance(value, bool),
                "bool": isinstance(value, bool),
            }.get(type_name, False)
            if not valid:
                return None, "schema_failure"
        try:
            self._tool_validation.validate(name, args, tool)
        except Exception:
            return None, "invalid_args"
        return ToolCall(f"legacy-recovered-{uuid.uuid4().hex}", name, args), None


class LegacyModelInvoker:
    """Single legacy-text model invocation seam.

    Native-tool orchestration remains in the current lifecycle until the
    complete loop migration; this adapter deliberately has no retry policy.
    """

    def __init__(self, model_client, max_new_tokens):
        self._model_client = model_client
        self._max_new_tokens = max_new_tokens
        self._client_lock = _model_client_lock(model_client)
        # Compatibility-only diagnostic state.  Runtime persistence consumes
        # ModelInvocationResult.completion_metadata, never this attribute.
        self.last_completion_metadata = {}

    def invoke(self, *, protocol, prompt="", messages=None, tools=None, tool_choice=None, on_delta=None, model_kwargs=None):
        """Invoke one existing Codecub model protocol and capture its metadata."""
        kwargs = dict(model_kwargs or {})
        with self._client_lock:
            if protocol == "native_tools":
                result = self._model_client.complete_with_tools(
                    messages or [], tools or [], self._max_new_tokens, tool_choice=tool_choice
                )
            elif protocol == "legacy_stream":
                result = self._model_client.stream_complete(
                    prompt, self._max_new_tokens, on_delta=on_delta, **kwargs
                )
            elif protocol == "legacy_text":
                result = self._model_client.complete(prompt, self._max_new_tokens, **kwargs)
            else:
                raise ValueError(f"unsupported model protocol: {protocol}")
            self.last_completion_metadata = dict(
                getattr(self._model_client, "last_completion_metadata", {}) or {}
            )
            return ModelInvocationResult(result, dict(self.last_completion_metadata))

    def invoke_text(self, prompt, *, on_delta=None, model_kwargs=None):
        protocol = "legacy_stream" if hasattr(self._model_client, "stream_complete") else "legacy_text"
        return self.invoke(protocol=protocol, prompt=prompt, on_delta=on_delta, model_kwargs=model_kwargs).response


class LoopHistory:
    """Persist loop-produced history records without depending on Runtime."""

    def __init__(self, session, session_manager):
        self._session = session
        self._session_manager = session_manager
        self.last_path = None
        self.source_reads = []
        self.run_usage = []

    @property
    def session_id(self):
        return str(self._session.get("id", ""))

    def begin_turn(self):
        self.source_reads = []
        self.run_usage = []

    def record(self, item):
        self._session["history"].append(dict(item))
        self.last_path = self._session_manager.save(self._session)
        return self.last_path


class LoopStatus:
    """Emit loop progress status through the existing app-event contract."""

    def __init__(self, event_handler=None, subject=None, event_handler_ref=None):
        self._event_handler_ref = event_handler_ref
        self._event_handler = event_handler
        self._subject = subject

    def bind(self, event_handler, subject):
        """Refresh the app event sink when the host wires it after construction."""
        self._event_handler = event_handler
        if self._event_handler_ref is not None:
            self._event_handler_ref["value"] = event_handler
        self._subject = subject

    def _handler(self):
        if self._event_handler_ref is not None:
            return self._event_handler_ref.get("value")
        return self._event_handler

    def emit(self, task_state, phase, label, detail="", started_at="", run_started_at=None):
        event_handler = self._handler() or getattr(self._subject, "event_handler", None)
        if event_handler is None:
            return
        import time
        event_handler("run_status", {
            "phase": phase, "label": label, "detail": detail,
            "started_at": started_at,
            "elapsed_ms": int((time.monotonic() - run_started_at) * 1000)
            if run_started_at is not None else 0,
        }, self._subject, task_state)

    def emit_app_event(self, event_name, task_state, payload=None):
        event_handler = self._handler() or getattr(self._subject, "event_handler", None)
        if event_handler is None:
            return
        event_handler(event_name, dict(payload or {}), self._subject, task_state)


class LegacyLoopStateAdapter:
    """Owns loop-state mutations and their observations without Runtime access."""

    def __init__(self, *, root, observer):
        self._root = root
        self._observer = observer
        self.working_state = None
        self.planning = {}
        self.watchdog = None
        self.edit_decision_watchdog = None
        self.protected_constraints = []
        self.last_prompt_metadata = {}
        self.requires_workspace_change = False
        self.source_read_count = 0
        self.runtime_mode = "interactive"

    def bind_turn(self, *, working_state, planning, watchdog, edit_decision_watchdog=None):
        """Bind fresh task-local state created by the composition root."""
        self.working_state = working_state
        self.planning = planning
        self.watchdog = watchdog
        if edit_decision_watchdog is not None:
            self.edit_decision_watchdog = edit_decision_watchdog

    def synchronize(self, *, working_state=None, planning=None, watchdog=None):
        """Refresh explicit state references for legacy direct-state callers."""
        if working_state is not None:
            self.working_state = working_state
        if planning is not None:
            self.planning = planning
        if watchdog is not None:
            self.watchdog = watchdog

    def bind_policy(self, *, requires_workspace_change=False, source_read_count=0,
                    requires_two_source_reads=False, last_prompt_metadata=None):
        self.requires_workspace_change = bool(requires_workspace_change)
        self.source_read_count = int(source_read_count or 0)
        self.requires_two_source_reads = bool(requires_two_source_reads)
        if last_prompt_metadata is not None:
            self.last_prompt_metadata = last_prompt_metadata

    def reset_watchdogs(self):
        def file_hash(path):
            return memorylib.file_freshness(path, self._root)

        self.watchdog = ProgressWatchdog(file_hash_fn=file_hash)
        self.edit_decision_watchdog = EditDecisionWatchdog(file_hash_fn=file_hash)

    def update_planning(self, name, args, metadata, tool_step):
        return self.update_planning_state(
            name, args, metadata, tool_step,
            requires_workspace_change=self.requires_workspace_change,
            source_read_count=self.source_read_count,
            requires_two_source_reads=self.requires_two_source_reads,
        )

    def apply_tool_result(self, name, args, metadata, result, task_state):
        state = self.working_state
        if state is None:
            return
        state.update_from_tool_event(
            name, args, metadata, result, task_state.tool_steps, self._root,
        )
        self._observer.emit(task_state, "working_state_updated", {
            "step": task_state.tool_steps,
            "changed_files": list(state.changed_files),
            "verification_status": state.verification[-1].get("status", "") if state.verification else "",
        })

    def record_read_evidence(self, args, result, step, *, freshness, hint):
        path = task_policy.canonical_path((args or {}).get("path"))
        if not path:
            return False
        entry = {"path": path, "start": int((args or {}).get("start", 1)),
                 "end": int((args or {}).get("end", 200)), "freshness": freshness,
                 "last_read_step": step, "hint": hint}
        import hashlib
        entry["marker"] = hashlib.sha256(
            f"{entry['path']}:{entry['start']}:{entry['end']}:{entry['freshness']}".encode("utf-8")
        ).hexdigest()[:12]
        ledger = self.planning["evidence_ledger"]
        ledger[:] = [prior for prior in ledger if not (
            prior["path"] == entry["path"] and prior["start"] == entry["start"]
            and prior["end"] == entry["end"] and prior["freshness"] == entry["freshness"]
        )]
        ledger.append(entry)
        return True

    def invalidate_evidence_for_paths(self, paths):
        changed = {task_policy.canonical_path(path) for path in (paths or [])}
        if not changed:
            return 0
        ledger = self.planning.get("evidence_ledger", [])
        retained = [entry for entry in ledger if entry["path"] not in changed]
        removed = len(ledger) - len(retained)
        self.planning["evidence_eviction_count"] += removed
        self.planning["evidence_ledger"] = retained
        return removed

    def observe_watchdog(self, task_state, name, args, metadata, result, step):
        """Advance watchdog state and emit only loop observations.

        The caller consumes the returned signal and decides whether to stop.
        """
        decision = self.watchdog.record_tool_event(name, args, metadata, result, step)
        for signal in decision.progress_signals:
            self._observer.emit(task_state, "progress_detected", {
                "kind": signal.kind, "reason": str(signal.reason), "step": signal.step,
            })
        if decision.suspected_now:
            self._observer.emit(task_state, "stuck_suspected", {
                "pattern": decision.stuck_pattern, "step": step,
                "no_progress_score": self.watchdog.no_progress_score,
            })
            self.watchdog.begin_recovery(step)
            self._observer.emit(task_state, "recovery_turn_started", {
                "step": step, "recovery_turn_count": self.watchdog.recovery_turn_count,
            })
        elif decision.recovered_now:
            self._observer.emit(task_state, "recovery_turn_finished", {
                "success": True, "step": step,
                "recovery_success_count": self.watchdog.recovery_success_count,
            })
        elif decision.confirmed_now:
            self._observer.emit(task_state, "stuck_confirmed", {
                "pattern": decision.stuck_pattern, "step": step,
                "stuck_confirmed_count": self.watchdog.stuck_confirmed_count,
            })
        return decision

    @staticmethod
    def _set_action_readiness(state, readiness, tool_step):
        if state["action_readiness"] == readiness:
            return False
        state["action_readiness"] = readiness
        state["action_readiness_transitions"].append(
            {"state": readiness, "tool_step": tool_step}
        )
        return True

    def update_planning_state(self, name, args, metadata, tool_step, *,
                              requires_workspace_change, source_read_count,
                              requires_two_source_reads):
        """Update task-local exploration/progress state after one tool event."""
        state = self.planning
        status = str((metadata or {}).get("tool_status", ""))
        if name in task_policy.ACTION_TOOLS:
            if state["first_action_step"] is None:
                state["first_action_step"] = tool_step
                state["exploration_steps_before_first_action"] = state["consecutive_exploration"]
            state["consecutive_exploration"] = 0
            state["seen_reads"].clear()
            state["seen_searches"].clear()
            if bool((metadata or {}).get("workspace_changed")):
                state["workspace_change_count"] += 1
                if state["first_workspace_change_step"] is None:
                    state["first_workspace_change_step"] = tool_step
                self._set_action_readiness(state, "action_taken", tool_step)
            if status == "rejected":
                state["rejected_steps"] += 1
            return False
        if status == "rejected":
            state["rejected_steps"] += 1
            return False
        if name == "run_shell":
            state["verification_steps"] += 1
            if state["first_execution_step"] is None:
                state["first_execution_step"] = tool_step
            if state["first_action_step"] is None:
                state["verification_before_first_action"] += 1
            epoch = state["workspace_change_count"]
            signature = (task_policy.normalize_shell_command(args), epoch)
            redundant = signature in state["seen_verifications"]
            state["seen_verifications"].add(signature)
            state["redundant_verification_steps" if redundant else "productive_verification_steps"] += 1
            if epoch > state["last_verified_change_count"]:
                if state["first_verification_after_change_step"] is None:
                    state["first_verification_after_change_step"] = tool_step
                state["last_verified_change_count"] = epoch
            return redundant
        if name not in task_policy.EXPLORATION_TOOLS:
            return False
        state["consecutive_exploration"] += 1
        if status == "ok" and requires_workspace_change and not state["workspace_change_count"]:
            if name == "read_file" and task_policy.is_source_path(args.get("path")):
                if not requires_two_source_reads or source_read_count >= 2:
                    self._set_action_readiness(state, "action_expected", tool_step)
            elif state["action_readiness"] == "unknown":
                self._set_action_readiness(state, "evidence_gathering", tool_step)
        redundant = False
        if name == "search":
            signature = task_policy.normalize_search(args)
            redundant = signature in state["seen_searches"]
            state["seen_searches"].add(signature)
        elif name == "read_file":
            path = task_policy.canonical_path(args.get("path"))
            prior = state["seen_reads"].setdefault(path, [])
            redundant = any(task_policy.read_overlap_ratio(args, previous) >= 0.8 for previous in prior)
            prior.append(dict(args))
        state["redundant_exploration_steps" if redundant else "productive_exploration_steps"] += 1
        return redundant

    def adopt_protected_constraint(self, task_state, message):
        """Store one injected constraint and observe its adoption exactly once."""
        message = str(message or "").strip()
        if not message:
            return False
        self.protected_constraints.append(message)
        self._observer.emit(task_state, "run_injected", {"message": message[:300]})
        return True


class LoopObserver:
    """Narrow trace fan-out for loop state; it has no Runtime dependency."""

    _SENSITIVE_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
    _CANONICAL_EVENT_NAMES = {
        "run_started": "run.started",
        "run_completed": "run.completed",
        "model_requested": "model.started",
        "tool_executed": "tool.completed",
        "workspace_changed": "workspace.changed",
    }

    def __init__(self, *, run_store, event_bus, session, trace_context, secret_values=(), usage_store=None, event_sink=None):
        self._run_store = run_store
        self._event_bus = event_bus
        self._session = session
        self._trace_context = trace_context
        self._secret_values = tuple(sorted((str(value) for value in secret_values if value), key=len, reverse=True))
        self._usage_store = usage_store
        self._event_sink = event_sink
        self._run_usage = []
        self._session_id = str(session.get("id", ""))
        self.last_prompt_metadata = {}
        self.last_completion_metadata = {}

    def bind_run(self, *, run_usage=None, session_id=None):
        self._run_usage = run_usage if run_usage is not None else []
        if session_id is not None:
            self._session_id = str(session_id)

    def record_usage(self, task_state, usage_record):
        """Persist one already-normalized model usage record and emit its event."""
        usage_record = self.redact_artifact(dict(usage_record or {}))
        self._run_usage.append(usage_record)
        if self._usage_store is None:
            return usage_record
        try:
            stored = self._usage_store.record(usage_record)
            run_snapshot = build_usage_snapshot(
                self._run_usage, "run", session_id=self._session_id,
                run_id=getattr(task_state, "run_id", ""),
            )
            session_snapshot = (
                (stored or {}).get("snapshot") if isinstance(stored, dict) else None
            )
            if isinstance(session_snapshot, dict):
                payload = {
                    "schema_version": 2,
                    "usage_id": usage_record.get("usage_id", ""),
                    "run_snapshot": run_snapshot,
                    "session_snapshot": session_snapshot,
                }
                if self._event_sink is not None:
                    self._event_sink("usage_updated", task_state, payload)
        except Exception as exc:
            self.emit(task_state, "usage_persistence_warning", {
                "error_type": exc.__class__.__name__,
                "message": self.redact_text(str(exc)),
            })
        self.emit(task_state, "model_usage_recorded", {
            "usage_id": usage_record.get("usage_id", ""),
            "connection_profile_id": usage_record.get("connection_profile_id", ""),
        })
        return usage_record

    @classmethod
    def _redact(cls, value, key=None, secret_values=()):
        key_upper = str(key or "").upper()
        if key and any(
            key_upper == marker
            or key_upper.endswith(marker)
            or key_upper.endswith(f"_{marker}")
            for marker in cls._SENSITIVE_MARKERS
        ):
            return "<redacted>"
        if isinstance(value, dict):
            return {str(k): cls._redact(v, k, secret_values) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._redact(item, key, secret_values) for item in value]
        if isinstance(value, str):
            for secret in secret_values:
                value = value.replace(secret, "<redacted>")
        return value

    def emit(self, task_state, event, payload=None):
        payload = self._redact(dict(payload or {}), secret_values=self._secret_values)
        for key, value in self._trace_context.items():
            if value and not payload.get(key):
                payload[key] = value
        if getattr(task_state, "run_id", "") and not payload.get("run_id"):
            payload["run_id"] = task_state.run_id
        payload["event"] = event
        payload["created_at"] = now()
        self._run_store.append_trace(task_state, payload)
        self._event_bus.emit(self._CANONICAL_EVENT_NAMES.get(event, str(event).replace("_", ".")), run_id=getattr(task_state, "run_id", ""),
                             agent_id=self._session.get("id", ""), payload=payload)
        return payload

    def redact_text(self, value):
        """Redact loop-visible text with the same secret set as trace emission."""
        return self._redact(str(value), secret_values=self._secret_values)

    def redact_artifact(self, value):
        """Redact a nested loop artifact before it reaches persistent storage."""
        return self._redact(value, secret_values=self._secret_values)
