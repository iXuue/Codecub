"""Surface-free model/tool loop boundary."""

from __future__ import annotations
import json
import re
import time

from .. import task_policy
from .. import tools as toolkit
from ..models import ModelResponse
from ..workspace import clip, now

from .runner import LoopOutcome


DEFAULT_INTERACTIVE_EMERGENCY_CAP = 500
DEFAULT_INTERACTIVE_ATTEMPT_CAP = 1200
RUNTIME_MODE_INTERACTIVE = "interactive"
CHECKPOINT_PARTIAL_STALE_STATUS = "partial-stale"
CHECKPOINT_WORKSPACE_MISMATCH_STATUS = "workspace-mismatch"
RECOVERY_TURN_PROMPT = (
    "Your recent actions have not produced new evidence, workspace changes, "
    "or new verification information.\n\n"
    "Summarize:\n"
    "1. what is already known,\n"
    "2. the current blocker,\n"
    "3. why the recent strategy is not progressing,\n"
    "4. choose a materially different next action.\n\n"
    "Do not repeat the same search/read/test pattern."
)


class FinalAnswerDeltaFilter:
    """Stream only the contents of a legacy ``<final>`` answer."""

    start_tag = "<final>"
    end_tag = "</final>"

    def __init__(self, on_text):
        self.on_text = on_text
        self.buffer = ""
        self.in_final = False
        self.closed = False

    def feed(self, chunk):
        if self.closed or not chunk:
            return
        self.buffer += str(chunk)
        if not self.in_final:
            start = self.buffer.find(self.start_tag)
            tool_start = self.buffer.find("<tool")
            if tool_start != -1 and (start == -1 or tool_start < start):
                self.buffer = self.buffer[-(len(self.start_tag) - 1):]
                return
            if start == -1:
                self.buffer = self.buffer[-(len(self.start_tag) - 1):]
                return
            self.buffer = self.buffer[start + len(self.start_tag):]
            self.in_final = True
        while self.in_final and self.buffer:
            end = self.buffer.find(self.end_tag)
            if end >= 0:
                if end > 0:
                    self.on_text(self.buffer[:end])
                self.buffer = self.buffer[end + len(self.end_tag):]
                self.closed = True
                return
            safe_length = self._safe_emit_length()
            if safe_length <= 0:
                return
            self.on_text(self.buffer[:safe_length])
            self.buffer = self.buffer[safe_length:]

    def _safe_emit_length(self):
        max_tail = min(len(self.end_tag) - 1, len(self.buffer))
        for tail_length in range(max_tail, 0, -1):
            if self.end_tag.startswith(self.buffer[-tail_length:]):
                return len(self.buffer) - tail_length
        return len(self.buffer)


class AgentLoop:
    """Own the deterministic model→tool loop for one prepared turn.

    This class is deliberately constructed from explicit collaborators.  The
    Runtime composes it, but is not visible to the loop and cannot be called
    back into by the loop.
    """

    def __init__(self, observer, loop_state, context, model_invoker, tool_executor,
                 injection_source, cancellation, history, run_store, status, hooks,
                 model_client, requires_workspace_change, step_budget, emergency_cap,
                 runtime_mode, prompt_cache_enabled, tools, prefix):
        self._observer = observer
        self._loop_state = loop_state
        self._context = context
        self._model_invoker = model_invoker
        self._tool_executor = tool_executor
        self._injection_source = injection_source
        self._cancellation = cancellation
        self._history = history
        self._run_store = run_store
        self._status = status
        self._hooks = hooks
        self._model_client = model_client
        self._requires_workspace_change = bool(requires_workspace_change)
        self._step_budget = step_budget
        self._emergency_cap = emergency_cap
        self._runtime_mode = runtime_mode
        self._prompt_cache_enabled = bool(prompt_cache_enabled)
        self._tools = tools
        self._prefix = prefix

    def bind_loop_state(self, loop_state):
        """Refresh the explicit state collaborator at a turn boundary."""
        self._loop_state = loop_state

    def bind_collaborators(self, *, context, model_invoker, tool_executor, loop_state, injection_source, cancellation, history, run_store, status):
        self._context = context
        self._model_invoker = model_invoker
        self._tool_executor = tool_executor
        self._loop_state = loop_state
        self._injection_source = injection_source
        self._cancellation = cancellation
        self._history = history
        self._run_store = run_store
        self._status = status

    def bind_loop_config(self, *, prefix):
        self._prefix = prefix

    def _validate_provider_context(self, task_state, prompt_metadata, *, protocol):
        """Run the single validation gate for one provider-bound assembly."""

        validator = getattr(self._context, "validate_context", None)
        if not callable(validator):
            return None
        result = validator(protocol=protocol)
        if result is None:
            return None
        if hasattr(result, "to_dict"):
            evidence = result.to_dict()
        elif isinstance(result, dict):
            evidence = dict(result)
        else:
            evidence = {
                "status": str(getattr(result, "status", "INVALID")),
                "action": str(getattr(result, "action", "REJECT")),
                "valid": bool(getattr(result, "valid", False)),
            }
        prompt_metadata["context_validation"] = evidence
        prompt_metadata["validation_status"] = evidence.get(
            "status", str(getattr(result, "status", "INVALID"))
        )
        prompt_metadata["validation_action"] = evidence.get(
            "action", str(getattr(result, "action", "REJECT"))
        )
        self._context.set_last_prompt_metadata(prompt_metadata)
        self._observer.emit(
            task_state,
            "context_validated",
            {"validation": evidence},
        )
        return result

    @staticmethod
    def _validation_decision(result):
        if result is None:
            return True, "ALLOW", ""
        if isinstance(result, dict):
            evidence = result.get("evidence") or result
            status = str(result.get("status", evidence.get("status", "INVALID")))
            action = str(result.get("action", evidence.get("action", "REJECT")))
            valid = bool(result.get("valid", status in {"VALID", "VALID_WITH_FALLBACK"}))
            error = str(result.get("error", ""))
            if not error:
                error = "; ".join(
                    str(item)
                    for item in evidence.get("hard_failures", [])
                    or evidence.get("failed_checks", [])
                )
        else:
            status = str(getattr(result, "status", "INVALID"))
            action = str(getattr(result, "action", "REJECT"))
            valid = bool(getattr(result, "valid", False))
            error = str(getattr(result, "error", "") or "")
            if not error:
                error = "; ".join(str(item) for item in getattr(result, "failed_checks", ()) or ())
        return valid and action == "ALLOW", action, error

    def _emit_model_requested(self, task_state, prompt_metadata):
        self._observer.emit(
            task_state,
            "model_requested",
            {
                "attempts": task_state.attempts,
                "tool_steps": task_state.tool_steps,
                "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
            },
        )

    def _drain_injections(self, task_state):
        """Adopt queued constraints at the loop's safe iteration boundary."""
        if self._injection_source is None:
            return []
        injected = list(self._injection_source() or [])
        for item in injected:
            self._loop_state.adopt_protected_constraint(
                task_state, str(getattr(item, "message", item)).strip()
            )
        return injected

    @staticmethod
    def _retry_notice(problem=None):
        prefix = f"Runtime notice: {problem}" if problem else "Runtime notice: model returned malformed tool output"
        return (
            f"{prefix}. Reply with a valid <tool> call or a non-empty <final> answer. "
            'For multi-line files, prefer <tool name="write_file" path="file.py"><content>...</content></tool>.'
        )

    @staticmethod
    def _extract(text, tag, *, raw=False):
        start_tag, end_tag = f"<{tag}>", f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:] if raw else text[start:].strip()
        return text[start:end] if raw else text[start:end].strip()

    @staticmethod
    def _parse_xml_tool(raw):
        match = re.search(r"<tool(?P<attrs>[^>]*)>(?P<body>.*?)</tool>", raw, re.S)
        if not match:
            return None
        attrs = {
            item.group(1): item.group(2) if item.group(2) is not None else item.group(3)
            for item in re.finditer(r'''([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')''', match.group("attrs"))
        }
        name = str(attrs.pop("name", "")).strip()
        if not name:
            return None
        body, args = match.group("body"), dict(attrs)
        for key in ("content", "old_text", "new_text", "command", "task", "pattern", "path"):
            if f"<{key}>" in body:
                args[key] = AgentLoop._extract(body, key, raw=True)
        body_text = body.strip("\n")
        if name == "write_file" and "content" not in args and body_text:
            args["content"] = body_text
        if name == "delegate" and "task" not in args and body_text:
            args["task"] = body_text.strip()
        return {"name": name, "args": args}

    @classmethod
    def _parse_model_response(cls, raw):
        raw = str(raw)
        if "<tool>" in raw and ("<final>" not in raw or raw.find("<tool>") < raw.find("<final>")):
            try:
                payload = json.loads(cls._extract(raw, "tool"))
            except Exception:
                return "retry", cls._retry_notice("model returned malformed tool JSON")
            if not isinstance(payload, dict):
                return "retry", cls._retry_notice("tool payload must be a JSON object")
            if not str(payload.get("name", "")).strip():
                return "retry", cls._retry_notice("tool payload is missing a tool name")
            if payload.get("args", {}) is None:
                payload["args"] = {}
            elif not isinstance(payload.get("args", {}), dict):
                return "retry", cls._retry_notice()
            return "tool", payload
        if "<tool" in raw and ("<final>" not in raw or raw.find("<tool") < raw.find("<final>")):
            payload = cls._parse_xml_tool(raw)
            return ("tool", payload) if payload is not None else ("retry", cls._retry_notice())
        if "<final>" in raw:
            final = cls._extract(raw, "final").strip()
            return ("final", final) if final else ("retry", cls._retry_notice("model returned an empty <final> answer"))
        return ("retry", cls._retry_notice("model response is missing required <tool> or <final> tags")) if raw.strip() else ("retry", cls._retry_notice("model returned an empty response"))

    def run(self, user_message, run_id="", preparation=None):
        """执行一次完整的 agent 回合，直到产出最终答案或命中停止条件。

        作用：
        把一条用户请求扩展成可持续推进的控制循环：组装 prompt、调用模型、
        执行工具、更新显式 loop state，直到模型给出最终答案或系统主动停下。

        输入 / 输出：
        - 输入：`user_message`，即用户这一次的任务描述
        - 输出：字符串形式的最终回答；如果中途达到步数上限或重试上限，
          返回的是一条停止原因说明

        它只产生 ``LoopOutcome``；TurnRunner 负责把结果映射到会话和终态持久化。
        """
        if preparation is None:
            raise ValueError("AgentLoop.run requires TurnRunner preparation")
        run_started_at = preparation.started_at
        run_started_wall = preparation.started_wall
        task_state = preparation.task_state
        source_reads = self._history.source_reads
        run_usage = self._history.run_usage
        session_id = self._history.session_id
        self._observer.bind_run(run_usage=run_usage, session_id=session_id)

        tool_steps = 0
        attempts = 0
        research_steps = 0
        research_budget = task_policy.research_tool_budget(user_message)
        finalization_required = False
        finalization_rejections = 0
        validation_failures = 0
        # Phase 1: 每次 ask() 独立跟踪 stuck 状态，不跨 run 累积。
        step_budget = self._step_budget
        attempt_cap = (
            max(step_budget * 3, step_budget + 4)
            if step_budget is not None
            else DEFAULT_INTERACTIVE_ATTEMPT_CAP
        )
        emergency_cap = (
            None
            if step_budget is not None
            else int(self._emergency_cap or DEFAULT_INTERACTIVE_EMERGENCY_CAP)
        )
        native_mode = bool(getattr(self._model_client, "supports_native_tools", False))
        native_messages = []
        pending_native_calls = []
        if native_mode:
            native_messages = self._context.initial_native_messages(user_message)
            self._observer.emit(
                task_state,
                "model_protocol_selected",
                {
                    "model_protocol": "native_tools",
                    "provider_protocol": getattr(
                        getattr(self._model_client, "connection_profile", None),
                        "protocol",
                        "",
                    ),
                },
            )
        else:
            self._observer.emit(
                task_state,
                "model_protocol_selected",
                {
                    "model_protocol": "legacy_text",
                    "provider_protocol": getattr(
                        getattr(self._model_client, "connection_profile", None),
                        "protocol",
                        "",
                    ),
                },
            )

        # 这是 agent 的主循环，可以按“感知 -> 决策 -> 行动 -> 记录”来理解：
        # 1. 感知：重新组 prompt，把当前状态整理给模型看
        # 2. 决策：让模型返回一个工具调用，或一个最终答案
        # 3. 行动：如果是工具调用，就执行工具
        # 4. 记录：把结果写回 history / task_state / trace / memory
        # 然后进入下一轮，直到停机条件满足。
        #
        # Phase 1 停机语义：
        # - experiment / 显式 max_steps：固定预算 + retry 上限，语义不变；
        # - interactive：没有小固定步数预算，只由 emergency cap（兜底）
        #   与 Progress Watchdog（stuck 判定）决定停止。
        while True:
            self._drain_injections(task_state)
            if self._cancellation.requested(task_state):
                return self.cancelled(task_state, user_message, run_started_at, run_started_wall)
            if step_budget is not None:
                if tool_steps >= step_budget or attempts >= attempt_cap:
                    return self.limited(task_state, user_message, attempts, attempt_cap, tool_steps,
                                        step_budget, run_started_at, run_started_wall)
            else:
                if tool_steps >= emergency_cap:
                    return self.emergency_cap(task_state, user_message, emergency_cap,
                                              run_started_at, run_started_wall)
                if attempts >= attempt_cap:
                    return self.limited(task_state, user_message, attempts, attempt_cap, tool_steps,
                                        step_budget, run_started_at, run_started_wall)
            attempts += 1
            task_state.record_attempt()
            self._run_store.write_task_state(task_state)
            self._status.emit(
                task_state,
                "building_context",
                "Building context",
                started_at=run_started_wall,
                run_started_at=run_started_at,
            )
            prompt_started_at = time.monotonic()

            def emit_context_status(phase, label, detail=""):
                self._status.emit(
                    task_state,
                    phase,
                    label,
                    detail=detail,
                    started_at=run_started_wall,
                    run_started_at=run_started_at,
                )
                self._observer.emit(
                    task_state,
                    "context_step_started",
                    {
                        "phase": phase,
                        "detail": clip(detail, 300),
                    },
                )

            prompt, prompt_metadata = self._context.build(
                user_message,
                status_callback=emit_context_status,
                task_state=task_state,
            )
            if native_mode:
                prompt_metadata["model_protocol"] = "native_tools"
            self._observer.emit(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                },
            )
            # 说明已有检查点的关键文件部分过期（内容变了）
            if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
                checkpoint = self._context.create_checkpoint(
                    task_state, user_message, trigger="freshness_mismatch"
                )
                self._run_store.write_task_state(task_state)
                self._observer.emit(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "freshness_mismatch",
                    },
                )
            # 说明运行环境/工作区指纹不一致（如 cwd、模型、工具签名等变化）
            elif (
                prompt_metadata.get("resume_status")
                == CHECKPOINT_WORKSPACE_MISMATCH_STATUS
            ):
                self._observer.emit(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(
                            prompt_metadata.get("runtime_identity_mismatch_fields", [])
                        ),
                    },
                )
                checkpoint = self._context.create_checkpoint(
                    task_state, user_message, trigger="workspace_mismatch"
                )
                self._run_store.write_task_state(task_state)
                self._observer.emit(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "workspace_mismatch",
                    },
                )
            # 当 prompt 预算被削减（旧 context_reduction 或 Phase 2 Compiler 压缩）时，
            # 也会创建检查点，保证 resume 不变量。
            compiler_compression = False
            if self._context.context_compiler is not None:
                current_count = self._context.context_compiler.compression_count
                compiler_compression = current_count > getattr(
                    self, "_last_compiler_compression_count", 0
                )
                self._last_compiler_compression_count = current_count
            if prompt_metadata.get("budget_reductions") or compiler_compression:
                checkpoint = self._context.create_checkpoint(
                    task_state, user_message, trigger="context_reduction"
                )
                self._run_store.write_task_state(task_state)
                self._observer.emit(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "context_reduction",
                    },
                )
            if self._cancellation.requested(task_state):
                return self.cancelled(task_state, user_message, run_started_at, run_started_wall)
            self._status.emit(
                task_state,
                "model_request",
                "Requesting model response",
                detail=str(getattr(self._model_client, "model", "")),
                started_at=run_started_wall,
                run_started_at=run_started_at,
            )
            prompt_cache_key = None
            prompt_cache_retention = None
            if self._prompt_cache_enabled and getattr(
                self._model_client, "supports_prompt_cache", False
            ):
                # 只有后端明确支持时，才把稳定前缀的 hash 作为 cache key 发出去。
                prompt_cache_key = prompt_metadata.get("prompt_cache_key")
                prompt_cache_retention = "in_memory"
            model_started_at = time.monotonic()
            self._status.emit(
                task_state,
                "model_streaming",
                "Receiving model response",
                detail=str(getattr(self._model_client, "model", "")),
                started_at=run_started_wall,
                run_started_at=run_started_at,
            )
            stream_filter = FinalAnswerDeltaFilter(
                lambda text: self._status.emit_app_event(
                    "assistant_delta", task_state, {"text": text}
                )
            )
            self._context.set_last_prompt_metadata(prompt_metadata)
            invocation = None
            try:
                model_kwargs = {
                    "prompt_cache_key": prompt_cache_key,
                    "prompt_cache_retention": prompt_cache_retention,
                }
                if getattr(
                    self._model_client, "supports_structured_prompt_cache", False
                ):
                    model_kwargs["stable_prefix"] = self._prefix
                if native_mode:
                    connection_profile = getattr(
                        self._model_client, "connection_profile", None
                    )
                    supports_tool_choice = bool(
                        getattr(connection_profile, "supports_tool_choice", False)
                    )
                    edit_decision = (
                        self._requires_workspace_change
                        and self._loop_state.planning.get("action_readiness")
                        == "action_expected"
                        and not self._loop_state.planning.get("workspace_change_count")
                    )
                    native_tools = toolkit.native_tool_definitions(self._tools)
                    tool_choice = (
                        "auto"
                        if connection_profile is None or supports_tool_choice
                        else None
                    )
                    # Do not insert an edit-decision user turn until every
                    # result for an earlier native tool-call batch is present.
                    if edit_decision and not pending_native_calls:
                        if connection_profile is None or supports_tool_choice:
                            native_tools = [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "submit_edit_decision",
                                        "description": "Submit exactly one edit proposal or one bounded evidence request.",
                                        "parameters": {
                                            "type": "object",
                                            "properties": {
                                                "decision": {
                                                    "type": "string",
                                                    "enum": ["edit", "need_evidence"],
                                                },
                                                "tool": {"type": "string"},
                                                "arguments": {"type": "object"},
                                            },
                                            "required": [
                                                "decision",
                                                "tool",
                                                "arguments",
                                            ],
                                            "additionalProperties": False,
                                        },
                                    },
                                }
                            ]
                            tool_choice = (
                                "required" if supports_tool_choice else tool_choice
                            )
                        else:
                            # Some OpenAI-compatible providers accept native tools but
                            # reject tool_choice.  Keep the real tool schema in this
                            # phase: replacing it with a synthetic decision function
                            # can yield stale calls to tools that are no longer listed.
                            # The direct-call compatibility branch below still records
                            # the same bounded edit or evidence decision.
                            native_messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Edit-decision phase: choose one direct native "
                                        "tool call. Use patch_file or write_file for the "
                                        "smallest justified edit; request read_file, search, "
                                        "or symbol_search only for essential missing evidence."
                                    ),
                                }
                            )
                        self._observer.emit(
                            task_state,
                            "phase_transition",
                            {
                                "phase": "edit_decision",
                                "edit_decision_index": self._loop_state.planning.get(
                                    "edit_decision_count", 0
                                )
                                + 1,
                                "compatibility_mode": not supports_tool_choice,
                            },
                        )
                    if pending_native_calls:
                        raw = pending_native_calls.pop(0)
                    else:
                        # ContextAssembler owns native provider-bound assembly;
                        # the existing compiler remains responsible for its
                        # compression, freshness, range and budget semantics.
                        compiled_native, compiler_meta = self._context.assemble_native(
                            user_message,
                            working_state=self._loop_state.working_state,
                            native_messages=native_messages,
                        )
                        if compiler_meta.get("should_compress"):
                            self._observer.emit(
                                task_state,
                                "compression_triggered",
                                {
                                    "mode": "native",
                                    "estimated_tokens": compiler_meta.get(
                                        "candidate_context_tokens"
                                    ),
                                    "usable_input_budget": compiler_meta.get(
                                        "usable_input_budget"
                                    ),
                                    "compression_count": compiler_meta.get(
                                        "compression_count"
                                    ),
                                },
                            )
                            self._observer.emit(
                                task_state,
                                "compression_started",
                                {"mode": "native"},
                            )
                            self._observer.emit(
                                task_state,
                                "compression_finished",
                                {
                                    "mode": "native",
                                    "compiled_tokens": compiler_meta.get(
                                        "compiled_context_tokens"
                                    ),
                                    "compression_count": compiler_meta.get(
                                        "compression_count"
                                    ),
                                },
                            )
                        native_messages = compiled_native
                        prompt_metadata.update(
                            {
                                "context_compiler": compiler_meta,
                                "compression_count": compiler_meta.get(
                                    "compression_count", 0
                                ),
                            }
                        )
                        validation = self._validate_provider_context(
                            task_state, prompt_metadata, protocol="native_tools"
                        )
                        allowed, action, validation_error = self._validation_decision(
                            validation
                        )
                        if not allowed:
                            max_validation_attempts = int(
                                getattr(
                                    getattr(self._context, "context_validator", None),
                                    "max_validation_attempts",
                                    1,
                                )
                                or 0
                            )
                            if action == "RETRY_ASSEMBLY" and validation_failures < max_validation_attempts:
                                validation_failures += 1
                                self._observer.emit(
                                    task_state,
                                    "context_validation_retry",
                                    {
                                        "attempt": validation_failures,
                                        "max_attempts": max_validation_attempts,
                                        "reason": validation_error,
                                    },
                                )
                                continue
                            return self.model_error(
                                task_state,
                                user_message,
                                "ContextValidationError",
                                validation_error or "provider-bound native context is invalid",
                                model_started_at,
                                run_started_at,
                                run_started_wall,
                            )
                        validation_failures = 0
                        self._emit_model_requested(task_state, prompt_metadata)
                        self._hooks.before_model(
                            self, task_state=task_state, prompt_metadata=prompt_metadata
                        )
                        invocation = self._model_invoker.invoke(
                            protocol="native_tools", messages=native_messages,
                            tools=native_tools, tool_choice=tool_choice,
                        )
                elif hasattr(self._model_client, "stream_complete"):
                    validation = self._validate_provider_context(
                        task_state, prompt_metadata, protocol="legacy_stream"
                    )
                    allowed, action, validation_error = self._validation_decision(validation)
                    if not allowed:
                        max_validation_attempts = int(
                            getattr(
                                getattr(self._context, "context_validator", None),
                                "max_validation_attempts",
                                1,
                            )
                            or 0
                        )
                        if action == "RETRY_ASSEMBLY" and validation_failures < max_validation_attempts:
                            validation_failures += 1
                            self._observer.emit(
                                task_state,
                                "context_validation_retry",
                                {
                                    "attempt": validation_failures,
                                    "max_attempts": max_validation_attempts,
                                    "reason": validation_error,
                                },
                            )
                            continue
                        return self.model_error(
                            task_state,
                            user_message,
                            "ContextValidationError",
                            validation_error or "provider-bound streaming context is invalid",
                            model_started_at,
                            run_started_at,
                            run_started_wall,
                        )
                    validation_failures = 0
                    self._emit_model_requested(task_state, prompt_metadata)
                    self._hooks.before_model(
                        self, task_state=task_state, prompt_metadata=prompt_metadata
                    )
                    invocation = self._model_invoker.invoke(
                        protocol="legacy_stream", prompt=prompt,
                        on_delta=stream_filter.feed, model_kwargs=model_kwargs,
                    )
                else:
                    validation = self._validate_provider_context(
                        task_state, prompt_metadata, protocol="legacy_text"
                    )
                    allowed, action, validation_error = self._validation_decision(validation)
                    if not allowed:
                        max_validation_attempts = int(
                            getattr(
                                getattr(self._context, "context_validator", None),
                                "max_validation_attempts",
                                1,
                            )
                            or 0
                        )
                        if action == "RETRY_ASSEMBLY" and validation_failures < max_validation_attempts:
                            validation_failures += 1
                            self._observer.emit(
                                task_state,
                                "context_validation_retry",
                                {
                                    "attempt": validation_failures,
                                    "max_attempts": max_validation_attempts,
                                    "reason": validation_error,
                                },
                            )
                            continue
                        return self.model_error(
                            task_state,
                            user_message,
                            "ContextValidationError",
                            validation_error or "provider-bound text context is invalid",
                            model_started_at,
                            run_started_at,
                            run_started_wall,
                        )
                    validation_failures = 0
                    self._emit_model_requested(task_state, prompt_metadata)
                    self._hooks.before_model(
                        self, task_state=task_state, prompt_metadata=prompt_metadata
                    )
                    invocation = self._model_invoker.invoke(
                        protocol="legacy_text", prompt=prompt, model_kwargs=model_kwargs,
                    )
            except Exception as exc:
                self._hooks.on_error(self, error=exc, task_state=task_state, stage="model")
                return self.model_error(
                    task_state, user_message, exc.__class__.__name__, self._observer.redact_text(str(exc)),
                    model_started_at, run_started_at, run_started_wall,
                )
            if invocation is not None:
                raw = invocation.response
            self._hooks.after_model(self, task_state=task_state, response=raw)
            completion_metadata = dict(
                invocation.completion_metadata if invocation is not None else {}
            )
            if self._cancellation.requested(task_state):
                return self.cancelled(task_state, user_message, run_started_at, run_started_wall)
            if completion_metadata:
                # 把后端返回的 usage/cache 统计并回 prompt_metadata，
                # 方便统一写入 report 和 trace。
                prompt_metadata.update(completion_metadata)
            estimated_tokens = prompt_metadata.get("estimated_prompt_tokens")
            actual_tokens = completion_metadata.get("input_tokens")
            if (
                isinstance(estimated_tokens, int)
                and isinstance(actual_tokens, int)
                and actual_tokens > 0
            ):
                prompt_metadata["actual_input_tokens"] = actual_tokens
                # Phase 2.6: provider 实际 input tokens（与 raw/compiled 估计同场对比）。
                prompt_metadata["provider_actual_input_tokens"] = actual_tokens
                compiler_meta = prompt_metadata.get("context_compiler")
                if isinstance(compiler_meta, dict):
                    compiler_meta["provider_actual_input_tokens"] = actual_tokens
                prompt_metadata["token_estimation_error"] = (
                    abs(actual_tokens - estimated_tokens) / actual_tokens
                )
                if prompt_metadata.get("available_prompt_tokens"):
                    prompt_metadata["context_utilization"] = (
                        estimated_tokens / prompt_metadata["available_prompt_tokens"]
                    )
            usage_record = completion_metadata.get("usage_record")
            if isinstance(usage_record, dict):
                usage_record = dict(usage_record)
                usage_record.update(
                    {
                        "usage_id": f"{task_state.run_id}-request-{attempts}",
                        "session_id": session_id,
                        "run_id": task_state.run_id,
                        "turn_id": task_state.task_id,
                        "request_index": attempts,
                        "recorded_at": now(),
                        "duration_ms": int(
                            (time.monotonic() - model_started_at) * 1000
                        ),
                        "status": "completed",
                    }
                )
                self._run_store.append_usage(task_state, usage_record)
                self._observer.record_usage(task_state, usage_record)
            self._context.set_last_completion_metadata(completion_metadata)
            self._context.set_last_prompt_metadata(prompt_metadata)
            tool_call_source = (
                str((getattr(raw, "raw_metadata", {}) or {}).get("tool_call_source") or "native")
                if getattr(raw, "tool_calls", ())
                else ""
            )
            if native_mode and not getattr(raw, "tool_calls", ()):
                recovered_call, rejection_reason = self._context.recover_native_text_tool_call(
                    getattr(raw, "text", raw)
                )
                if recovered_call is not None or rejection_reason is not None:
                    self._loop_state.planning["legacy_tool_recovery_attempts"] = (
                        self._loop_state.planning.get("legacy_tool_recovery_attempts", 0) + 1
                    )
                    if recovered_call is None:
                        self._loop_state.planning["legacy_tool_recovery_rejected"] = (
                            self._loop_state.planning.get("legacy_tool_recovery_rejected", 0)
                            + 1
                        )
                        self._observer.emit(
                            task_state,
                            "legacy_tool_recovery_rejected",
                            {"rejection_reason": rejection_reason},
                        )
                    else:
                        self._loop_state.planning["legacy_tool_recovery_success"] = (
                            self._loop_state.planning.get("legacy_tool_recovery_success", 0)
                            + 1
                        )
                        self._observer.emit(
                            task_state,
                            "legacy_tool_recovery_success",
                            {
                                "tool": recovered_call.name,
                                "tool_call_source": "legacy_recovered",
                            },
                        )
                        metadata = dict(getattr(raw, "raw_metadata", {}) or {})
                        metadata["tool_call_source"] = "legacy_recovered"
                        raw = ModelResponse(
                            text="",
                            tool_calls=(recovered_call,),
                            finish_reason=str(getattr(raw, "finish_reason", "") or ""),
                            usage=dict(getattr(raw, "usage", {}) or {}),
                            raw_metadata=metadata,
                        )
                        tool_call_source = "legacy_recovered"
            if getattr(raw, "tool_calls", ()):
                self._loop_state.planning["native_tool_calls"] = (
                    self._loop_state.planning.get("native_tool_calls", 0)
                    + len(raw.tool_calls)
                )
            if native_mode:
                if raw.tool_calls:
                    # Providers may return a batch even when asked not to.  Preserve
                    # every call id and make the batch boundary explicit instead of
                    # silently executing concurrent mutations.  The first call is
                    # processed through the normal guarded path; remaining calls are
                    # returned as deferred so the next structured decision is based
                    # on the refreshed workspace.
                    call = raw.tool_calls[0]
                    fallback_direct_decision = (
                        not supports_tool_choice
                        and self._requires_workspace_change
                        and self._loop_state.planning.get("action_readiness")
                        == "action_expected"
                        and not self._loop_state.planning.get("workspace_change_count")
                        and call.name
                        in {
                            "patch_file",
                            "write_file",
                            "read_file",
                            "search",
                            "symbol_search",
                        }
                    )
                    if call.name == "submit_edit_decision" or fallback_direct_decision:
                        if fallback_direct_decision:
                            decision = (
                                "edit"
                                if call.name in {"patch_file", "write_file"}
                                else "need_evidence"
                            )
                            requested_name = call.name
                            requested_args = call.arguments
                            self._observer.emit(
                                task_state,
                                "native_direct_edit_decision",
                                {
                                    "decision": decision,
                                    "tool": requested_name,
                                    "compatibility_reason": "provider_omits_tool_choice",
                                },
                            )
                        else:
                            decision = call.arguments.get("decision")
                            requested_name = call.arguments.get("tool")
                            requested_args = call.arguments.get("arguments")
                        # Phase 2.6：取消小固定 hard-stop（edit_decision = 4）。
                        # 是否继续由“真实进展”决定（EditDecisionWatchdog 分类 +
                        # ProgressWatchdog suspected/recovery/confirmed 状态机）。
                        self._loop_state.planning["edit_decision_count"] = (
                            self._loop_state.planning.get("edit_decision_count", 0) + 1
                        )
                        self._loop_state.edit_decision_watchdog.record_decision(decision)
                        allowed = (
                            {"patch_file", "write_file"}
                            if decision == "edit"
                            else {"read_file", "search", "symbol_search"}
                        )
                        if (
                            decision not in {"edit", "need_evidence"}
                            or requested_name not in allowed
                            or not isinstance(requested_args, dict)
                        ):
                            self._loop_state.planning["invalid_edit_decision_count"] = (
                                self._loop_state.planning.get(
                                    "invalid_edit_decision_count", 0
                                )
                                + 1
                            )
                            kind, payload = (
                                "retry",
                                "Invalid edit decision; submit one allowed structured decision.",
                            )
                        elif decision == "need_evidence":
                            # 单次 need_evidence 仍只能使用受控 read/search/symbol 工具
                            # （安全边界不变）；取消的是“第几次”的小固定上限。
                            classification = (
                                self._loop_state.edit_decision_watchdog.classify_evidence_request(
                                    requested_name,
                                    requested_args,
                                    task_state.tool_steps,
                                )
                            )
                            if classification.progress:
                                self._loop_state.planning["evidence_request_count"] = (
                                    self._loop_state.planning.get(
                                        "evidence_request_count", 0
                                    )
                                    + 1
                                )
                                kind, payload = (
                                    "tool",
                                    {
                                        "name": requested_name,
                                        "args": requested_args,
                                        "tool_call_id": call.id,
                                        "edit_decision": decision,
                                        "tool_call_source": tool_call_source or "native",
                                    },
                                )
                            else:
                                # 重复 evidence 且无 workspace change / 文件 hash 未变：
                                # 拒绝执行（不烧真实工具步），并把 no-progress 事件喂给主
                                # Watchdog，使“重复 evidence”也能 suspected -> recovery
                                # -> stuck_confirmed。
                                self._loop_state.edit_decision_watchdog.record_no_progress(
                                    classification
                                )
                                self._observer.emit(
                                    task_state,
                                    "edit_decision_no_progress",
                                    {
                                        "tool": requested_name,
                                        "reason": classification.reason,
                                        "edit_decision_count": self._loop_state.planning.get(
                                            "edit_decision_count", 0
                                        ),
                                        "no_progress_streak": self._loop_state.edit_decision_watchdog.no_progress_streak,
                                    },
                                )
                                rejected_meta = {
                                    "tool_status": "rejected",
                                    "tool_error_code": "repeated_evidence",
                                    "security_event_type": "",
                                    "risk_level": "low",
                                    "read_only": True,
                                    "affected_paths": [],
                                    "workspace_changed": False,
                                    "diff_summary": [],
                                }
                                watchdog_decision = self._loop_state.observe_watchdog(
                                    task_state,
                                    requested_name,
                                    requested_args,
                                    rejected_meta,
                                    "rejected: repeated evidence request",
                                    # 拒绝的事件没有真实工具执行，tool_steps 不前进；
                                    # 用单调递增的 attempts 作为 watchdog step，
                                    # 保证 recovery 窗口能正常计时。
                                    task_state.attempts,
                                )
                                if watchdog_decision.suspected_now:
                                    # 重复 evidence 触发 stuck suspected：注入 Recovery
                                    # Turn（与工具执行路径一致），继续运行。
                                    self._history.record(
                                        {
                                            "role": "assistant",
                                            "content": RECOVERY_TURN_PROMPT,
                                            "created_at": now(),
                                        }
                                    )
                                    native_messages.append(
                                        {
                                            "role": "user",
                                            "content": RECOVERY_TURN_PROMPT,
                                        }
                                    )
                                    self._status.emit(
                                        task_state,
                                        "stuck_suspected",
                                        "Recovery turn",
                                        detail=watchdog_decision.stuck_pattern,
                                        started_at=run_started_wall,
                                        run_started_at=run_started_at,
                                    )
                                    self._run_store.write_task_state(task_state)
                                    continue
                                if watchdog_decision.confirmed_now:
                                    return self.stuck(
                                        task_state, user_message, runtime_mode=self._runtime_mode,
                                        pattern=self._loop_state.watchdog.current_pattern,
                                        last_reason=self._loop_state.watchdog.last_progress_reason,
                                        last_step=self._loop_state.watchdog.last_progress_step,
                                        interactive_mode=RUNTIME_MODE_INTERACTIVE,
                                        started_at=run_started_at, started_wall=run_started_wall,
                                    )
                                kind, payload = (
                                    "retry",
                                    "Repeated evidence request: this read/search/symbol "
                                    "repeats already-observed evidence without a workspace "
                                    "change. Make the smallest justified edit now, or "
                                    "request genuinely new evidence (new file, new range, "
                                    "new symbol, new search, or a re-read after a file change).",
                                )
                        else:
                            kind, payload = (
                                "tool",
                                {
                                    "name": requested_name,
                                    "args": requested_args,
                                    "tool_call_id": call.id,
                                    "edit_decision": decision,
                                    "tool_call_source": tool_call_source or "native",
                                },
                            )
                    else:
                        kind, payload = (
                            "tool",
                            {
                                "name": call.name,
                                "args": call.arguments,
                                "tool_call_id": call.id,
                                "tool_call_source": tool_call_source or "native",
                            },
                        )
                    if kind == "retry":
                        # A rejected first call is not retained in native
                        # history, so the next model request cannot contain an
                        # unanswered assistant tool call.  A rejected queued
                        # call, however, already belongs to an accepted batch
                        # and must receive a matching tool result.
                        if raw.raw_metadata.get("queued_native_call"):
                            native_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call.id,
                                    "content": f"error: {payload}",
                                }
                            )
                        else:
                            # Phase 2.6: 拒绝原因要能被 native 模型看到（不保留
                            # 未应答的 assistant tool call，因此补一条 user 消息）。
                            native_messages.append(
                                {"role": "user", "content": str(payload)}
                            )
                    elif not raw.raw_metadata.get("queued_native_call"):
                        native_messages.append(
                            {
                                "role": "assistant",
                                "content": raw.text or None,
                                "tool_calls": [
                                    {
                                        "id": item.id,
                                        "type": "function",
                                        "function": {
                                            "name": item.name,
                                            "arguments": json.dumps(item.arguments),
                                        },
                                    }
                                    for item in raw.tool_calls
                                ],
                            }
                        )
                        pending_native_calls.extend(
                            type(raw)(
                                tool_calls=(item,),
                                raw_metadata={
                                    "queued_native_call": True,
                                    "tool_call_source": tool_call_source or "native",
                                },
                            )
                            for item in raw.tool_calls[1:]
                        )
                    if len(raw.tool_calls) > 1:
                        self._observer.emit(
                            task_state,
                            "native_tool_batch_queued",
                            {
                                "received": len(raw.tool_calls),
                                "queued": len(raw.tool_calls) - 1,
                            },
                        )
                else:
                    final_text = str(raw.text or "").strip()
                    if not final_text:
                        # Phase 2.6（Probe B/C 暴露）：native 路径与 legacy 一致，
                        # 空 final 不算成功完成——拒绝并要求模型给出非空答案或工具
                        # 调用，避免“completed 但 final_answer 为空”的误导记录。
                        kind, payload = (
                            "retry",
                            self._retry_notice("model returned an empty final response"),
                        )
                        # 让 native 模型看到拒绝原因（空 final 没有未应答的
                        # assistant tool call，直接补一条 user 消息）。
                        native_messages.append(
                            {"role": "user", "content": str(payload)}
                        )
                    else:
                        kind, payload = "final", final_text
            else:
                kind, payload = self._parse_model_response(raw)
            self._observer.emit(
                task_state,
                "model_parsed",
                {
                    "kind": kind,
                    "tool_call_source": (
                        payload.get("tool_call_source", "")
                        if kind == "tool" and isinstance(payload, dict)
                        else ""
                    ),
                    "completion_metadata": completion_metadata,
                    "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                },
            )

            if kind == "tool":
                name = payload.get("name", "")
                args = payload.get("args", {})
                if finalization_required and task_policy.is_research_tool(name):
                    finalization_rejections += 1
                    notice = task_policy.finalization_notice(
                        source_reads, research_steps, research_budget
                    )
                    self._history.record(
                        {"role": "assistant", "content": notice, "created_at": now()}
                    )
                    self._observer.emit(
                        task_state,
                        "research_budget_exhausted",
                        {
                            "tool_name": name,
                            "research_steps": research_steps,
                            "research_budget": research_budget,
                            "finalization_rejections": finalization_rejections,
                        },
                    )
                    if finalization_rejections >= 2:
                        return self.finalization_failed(
                            task_state, user_message, run_started_at, run_started_wall
                        )
                    self._run_store.write_task_state(task_state)
                    continue
                tool_steps += 1
                task_state.record_tool(name)
                tool_started_at = time.monotonic()
                self._status.emit(
                    task_state,
                    "tool_running",
                    f"Executing tool: {name}",
                    detail=name,
                    started_at=run_started_wall,
                    run_started_at=run_started_at,
                )
                read_notice = ""
                read_classification = "new"
                if name == "read_file":
                    read_notice, read_classification = self._context.read_guard_notice(args)
                operation_key = (
                    payload.get("tool_call_id", "")
                    if payload.get("tool_call_source") != "legacy_recovered"
                    else ""
                )
                result = self._tool_executor.execute(name, args, operation_key=operation_key)
                tool_metadata = dict(self._tool_executor.last_metadata or {})
                # A tool may observe cancellation while it is running.  Stop
                # before recording evidence, working-state progress, history,
                # or any other loop-visible mutation after that boundary.
                if self._cancellation.requested(task_state):
                    return self.cancelled(task_state, user_message, run_started_at, run_started_wall)
                if read_notice:
                    result = f"{read_notice}\n\n{result}"
                if name == "read_file":
                    tool_metadata["read_evidence_classification"] = (
                        read_classification
                    )
                    if read_classification == "avoidable_repeated_read":
                        self._loop_state.planning["avoidable_repeated_read_calls"] += 1
                    elif read_classification == "evidence_evicted_reread":
                        self._loop_state.planning["evidence_evicted_reread_calls"] += 1
                    self._context.record_read_evidence(args, result, task_state.tool_steps)
                elif bool(
                    tool_metadata.get("workspace_changed")
                ):
                    self._context.invalidate_evidence_for_paths(
                        tool_metadata.get("affected_paths")
                    )
                self._history.record(
                    {
                        "role": "tool",
                        "name": name,
                        "args": args,
                        "content": result,
                        "created_at": now(),
                    }
                )
                if native_mode:
                    native_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": payload.get("tool_call_id", ""),
                            "content": result,
                        }
                    )
                if name == "read_file" and task_policy.is_source_path(args.get("path")):
                    source_reads.append(str(args.get("path")))
                if research_budget is not None and task_policy.is_research_tool(name):
                    research_steps += 1
                self._run_store.write_task_state(task_state)
                tool_event_payload = {
                    "name": name,
                    "args": args,
                    "tool_call_source": payload.get("tool_call_source", ""),
                    "result": clip(result, 500),
                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                    **tool_metadata,
                }
                self._observer.emit(task_state, "tool_executed", tool_event_payload)
                self._status.emit_app_event(
                    "tool_executed", task_state, dict(tool_event_payload)
                )
                requires_two_source_reads = (
                    native_mode
                    and getattr(
                        self._model_client, "connection_profile", None
                    ) is not None
                    and not bool(getattr(
                        getattr(self._model_client, "connection_profile", None),
                        "supports_tool_choice", False,
                    ))
                )
                bind_policy = getattr(self._loop_state, "bind_policy", None)
                if bind_policy is not None:
                    bind_policy(
                        requires_workspace_change=self._requires_workspace_change,
                        source_read_count=len(source_reads),
                        requires_two_source_reads=requires_two_source_reads,
                    )
                update_planning = getattr(self._loop_state, "update_planning", None)
                if update_planning is not None:
                    semantic_repeat = update_planning(
                        name, args, tool_metadata, task_state.tool_steps
                    )
                else:
                    semantic_repeat = self._loop_state.update_planning_state(
                        name, args, tool_metadata, task_state.tool_steps,
                        requires_workspace_change=self._requires_workspace_change,
                        source_read_count=len(source_reads),
                        requires_two_source_reads=requires_two_source_reads,
                    )
                self._loop_state.apply_tool_result(
                    name, args, tool_metadata, result, task_state
                )
                # Phase 3: blocker / relevant-symbol / changed-file 实质变化时
                # 重新 retrieval（避免每 tool step 全库检索）。
                if self._context.memory_v2_enabled() and (
                    self._context.memory_signature()
                    != self._context.memory_retrieval_signature
                ):
                    self._context.refresh_memory_retrieval(user_message)
                # A native assistant tool-call message must be followed by a
                # tool result for every call before any user message.  Providers
                # can return a batch despite advertising no parallel support;
                # defer decision feedback until its queued calls are drained.
                if (
                    native_mode
                    and payload.get("edit_decision")
                    and not pending_native_calls
                ):
                    decision = payload["edit_decision"]
                    if decision == "need_evidence":
                        # 真实执行的 evidence 登记进 EditDecisionWatchdog，
                        # 供后续重复检测（同范围 / 同 search / 同 symbol）。
                        self._loop_state.edit_decision_watchdog.mark_evidence_executed(
                            name, args, task_state.tool_steps
                        )
                        notice = (
                            "Edit decision recorded as need_evidence and executed. "
                            "Continue while each request adds genuinely new evidence "
                            "(new file, new range, new symbol, new search, or a re-read "
                            "after a file change); repeated evidence with no workspace "
                            "change will be rejected. When the evidence is sufficient, "
                            "make the smallest justified edit."
                        )
                    else:
                        notice = (
                            "Edit decision recorded as edit. "
                            "Run a focused verification command before finalizing."
                        )
                    native_messages.append({"role": "user", "content": notice})
                    self._observer.emit(
                        task_state,
                        "edit_decision_feedback",
                        {"decision": decision, "tool": name},
                    )
                if semantic_repeat:
                    self._observer.emit(
                        task_state,
                        "semantic_redundant_exploration",
                        {"tool_name": name, "tool_step": task_state.tool_steps},
                    )
                # 纯 observability：exploration / implementation warning 仍会发出，
                # 但它们不再直接导致停机。是否卡住只由 Progress Watchdog 判定。
                self._context.maybe_emit_exploration_warning(task_state)
                self._context.maybe_emit_implementation_warning(task_state)
                checkpoint = self._context.create_checkpoint(
                    task_state, user_message, trigger="tool_executed"
                )
                self._run_store.write_task_state(task_state)
                self._observer.emit(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "tool_executed",
                    },
                )
                # Phase 1: Progress Watchdog 是唯一的 stuck 决策来源。
                watchdog_decision = self._loop_state.observe_watchdog(
                    task_state, name, args, tool_metadata,
                    result, task_state.tool_steps,
                )
                if watchdog_decision.suspected_now:
                    # 第一次疑似卡住：注入 Recovery Turn 提示（不含任务答案），
                    # 继续运行，不直接结束。
                    self._history.record(
                        {
                            "role": "assistant",
                            "content": RECOVERY_TURN_PROMPT,
                            "created_at": now(),
                        }
                    )
                    if native_mode:
                        native_messages.append(
                            {"role": "user", "content": RECOVERY_TURN_PROMPT}
                        )
                    # Phase 3: recovery turn 是 retrieval trigger。
                    if self._context.memory_v2_enabled():
                        self._context.refresh_memory_retrieval(user_message)
                    self._status.emit(
                        task_state,
                        "stuck_suspected",
                        "Recovery turn",
                        detail=watchdog_decision.stuck_pattern,
                        started_at=run_started_wall,
                        run_started_at=run_started_at,
                    )
                    self._run_store.write_task_state(task_state)
                    continue
                if watchdog_decision.confirmed_now:
                    return self.stuck(
                        task_state, user_message, runtime_mode=self._runtime_mode,
                        pattern=self._loop_state.watchdog.current_pattern,
                        last_reason=self._loop_state.watchdog.last_progress_reason,
                        last_step=self._loop_state.watchdog.last_progress_step,
                        interactive_mode=RUNTIME_MODE_INTERACTIVE,
                        started_at=run_started_at, started_wall=run_started_wall,
                    )
                if research_budget is not None and research_steps >= research_budget:
                    finalization_required = True
                    notice = task_policy.finalization_notice(
                        source_reads, research_steps, research_budget
                    )
                    self._history.record(
                        {"role": "assistant", "content": notice, "created_at": now()}
                    )
                    self._observer.emit(
                        task_state,
                        "finalization_required",
                        {
                            "source_reads": list(source_reads),
                            "research_steps": research_steps,
                            "research_budget": research_budget,
                        },
                    )
                    self._status.emit(
                        task_state,
                        "finalization_required",
                        "Generating answer from collected evidence",
                        detail=f"{research_steps}/{research_budget}",
                        started_at=run_started_wall,
                        run_started_at=run_started_at,
                    )
                continue

            if kind == "retry":
                self._history.record(
                    {"role": "assistant", "content": payload, "created_at": now()}
                )
                self._run_store.write_task_state(task_state)
                continue

            if (
                task_policy.requires_source_evidence(user_message)
                and not source_reads
            ):
                notice = task_policy.evidence_retry_notice()
                self._history.record(
                    {"role": "assistant", "content": notice, "created_at": now()}
                )
                self._observer.emit(
                    task_state,
                    "evidence_insufficient",
                    {"required": "source_file_read", "source_reads": []},
                )
                self._run_store.write_task_state(task_state)
                continue
            if (
                native_mode
                and self._requires_workspace_change
                and self._loop_state.planning["workspace_change_count"]
                > self._loop_state.planning["last_verified_change_count"]
            ):
                notice = (
                    "A workspace change was made but has not been verified. "
                    "Run one focused verification command now, then provide the final answer."
                )
                self._history.record(
                    {"role": "assistant", "content": notice, "created_at": now()}
                )
                if native_mode:
                    native_messages.append({"role": "user", "content": notice})
                self._observer.emit(
                    task_state,
                    "verification_required_after_change",
                    {
                        "workspace_change_count": self._loop_state.planning[
                            "workspace_change_count"
                        ],
                        "last_verified_change_count": self._loop_state.planning[
                            "last_verified_change_count"
                        ],
                    },
                )
                self._run_store.write_task_state(task_state)
                continue
            # Native providers may return a normal finish with an empty text
            # field.  `raw` is a ModelResponse in that mode, never fallback
            # content for a final answer.
            final = str(payload or "").strip()
            return LoopOutcome(
                answer=final,
                kind="success",
                task_state=task_state,
                user_message=user_message,
                started_at=run_started_at,
                started_wall=run_started_wall,
            )

    @staticmethod
    def outcome(answer, kind, task_state, user_message, started_at, started_wall, metadata=None):
        """Construct a loop result without performing lifecycle side effects."""
        return LoopOutcome(
            answer=answer,
            kind=kind,
            task_state=task_state,
            user_message=user_message,
            started_at=started_at,
            started_wall=started_wall,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def cancelled(cls, task_state, user_message, started_at, started_wall):
        return cls.outcome("Canceled by user.", "cancelled", task_state, user_message, started_at, started_wall)

    @classmethod
    def emergency_cap(cls, task_state, user_message, cap, started_at, started_wall):
        return cls.outcome(
            f"Stopped after reaching the emergency step cap ({cap}) without a final answer.",
            "emergency_cap", task_state, user_message, started_at, started_wall,
            {"cap": int(cap or 0)},
        )

    @classmethod
    def limited(cls, task_state, user_message, attempts, attempt_cap, tool_steps, step_budget, started_at, started_wall):
        retry_limit = attempts >= attempt_cap and (step_budget is None or tool_steps < step_budget)
        answer = (
            "Stopped after too many malformed model responses without a valid tool call or final answer."
            if retry_limit else "Stopped after reaching the step limit without a final answer."
        )
        return cls.outcome(answer, "limited", task_state, user_message, started_at, started_wall,
                           {"retry_limit": retry_limit})

    @classmethod
    def stuck(cls, task_state, user_message, *, runtime_mode, pattern, last_reason, last_step, interactive_mode, started_at, started_wall):
        if runtime_mode == interactive_mode:
            answer = (
                "Agent paused because it appears stuck.\n"
                "Current blocker: repeated recovery turns did not produce new "
                "evidence, workspace changes, or verification information.\n"
                f"Last useful progress: {last_reason or 'the start of the task'} (step {last_step or 0})."
            )
        else:
            answer = f"Stopped because the agent appeared stuck and did not recover (pattern: {pattern or 'no_progress_window'})."
        return cls.outcome(answer, "stuck", task_state, user_message, started_at, started_wall)

    @classmethod
    def model_error(cls, task_state, user_message, error_type, error_message, model_started_at, started_at, started_wall):
        answer = f"Model error: {error_message}" if error_message else f"Model error: {error_type}"
        return cls.outcome(answer, "model_error", task_state, user_message, started_at, started_wall,
                           {"error_type": error_type, "error_message": error_message,
                            "model_started_at": model_started_at})

    @classmethod
    def finalization_failed(cls, task_state, user_message, started_at, started_wall):
        return cls.outcome(
            "Stopped because the model did not produce a final answer after the research budget was exhausted.",
            "finalization_failed", task_state, user_message, started_at, started_wall,
        )
