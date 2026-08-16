"""Agent 运行时核心逻辑。

Pico 就是包在模型外面的控制循环：负责组 prompt、解析模型输出、
校验并执行工具、写 trace、更新工作记忆，以及在合适的时候停下来。
"""

import json
import os
import re
import textwrap
import uuid
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import memory as memorylib
from .memory_v2 import MemoryV2
from .context_manager import ContextManager
from .context_compiler import (
    ContextBudget,
    ContextCompiler,
    HistoryCondenser,
    PINNED_PROJECT_RULES,
    PINNED_RUNTIME_MODE,
    PINNED_SAFETY,
    WorkingState,
)
from .code_index import CodeIndex
from .edit_decision import EditDecisionWatchdog
from .token_budget import resolve_prompt_budget, resolve_token_counter
from .run_store import RunStore
from .telemetry import aggregate_usage_records, build_usage_snapshot
from .usage_store import UsageStore
from .task_state import TaskState
from . import tools as toolkit
from . import task_policy
from .watchdog import ProgressWatchdog
from .workspace import IGNORED_PATH_NAMES, MAX_HISTORY, WorkspaceContext, clip, now

SENSITIVE_ENV_NAME_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
REDACTED_VALUE = "<redacted>"
DEFAULT_SHELL_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "PWD",
    "SHELL",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
)
DEFAULT_FEATURE_FLAGS = {
    "memory": True,
    "relevant_memory": True,
    # Phase 3: Memory 2.0（Evidence Store + Durable Memory + Bounded Retrieval）。
    # memory_v2 是 master 开关；evidence_memory / durable_memory 是子开关。
    # memory=False 仍表示“完全 Memory OFF”（兼容旧实验的 memory_off 变体）。
    "memory_v2": True,
    "evidence_memory": True,
    "durable_memory": True,
    "context_reduction": True,
    "context_compiler": True,
    "prompt_cache": True,
}
DEFAULT_MAX_STEPS = 80
DEFAULT_INTERACTIVE_EMERGENCY_CAP = 500
DEFAULT_INTERACTIVE_ATTEMPT_CAP = 1200
RUNTIME_MODE_INTERACTIVE = "interactive"
RUNTIME_MODE_EXPERIMENT = "experiment"
STOP_REASON_STUCK_CONFIRMED = "stuck_confirmed"
STOP_REASON_EMERGENCY_CAP_REACHED = "emergency_cap_reached"
# Phase 2.6：取消小固定 edit-decision hard-stop
# （EDIT_DECISION_ATTEMPT_BUDGET / EDIT_EVIDENCE_RETRY_BUDGET 已移除）。
# 是否继续由 EditDecisionWatchdog 的“真实进展”决定；无进展由 ProgressWatchdog
# 的 suspected -> recovery -> stuck_confirmed 状态机收尾。
REPEATED_NO_PROGRESS_LIMIT = 5
STOP_REASON_REPEATED_NO_PROGRESS = "repeated_no_progress"
EXPLORATION_WARNING_THRESHOLD = 6
SEMANTIC_REPEAT_WARNING_THRESHOLD = 2
SEMANTIC_REPEAT_HARD_STOP_THRESHOLD = 8
READ_OVERLAP_THRESHOLD = 0.8
EVIDENCE_LEDGER_LIMIT = 6
EVIDENCE_HINT_LIMIT = 280
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
CHECKPOINT_SCHEMA_VERSION = "phase1-v1"
CHECKPOINT_NONE_STATUS = "no-checkpoint"
CHECKPOINT_FULL_VALID_STATUS = "full-valid"
CHECKPOINT_PARTIAL_STALE_STATUS = "partial-stale"
CHECKPOINT_WORKSPACE_MISMATCH_STATUS = "workspace-mismatch"
CHECKPOINT_SCHEMA_MISMATCH_STATUS = "schema-mismatch"
DURABLE_MEMORY_INTENT_PATTERN = re.compile(
    r"(?i)\b(capture|remember|save|store|persist|note)\b"
)
DURABLE_MEMORY_INTENT_ZH_PATTERN = re.compile(
    r"(记住|保存|记录|沉淀|长期记忆|持久记忆)"
)
DURABLE_MEMORY_LINE_PATTERNS = (
    ("project-conventions", re.compile(r"(?i)^Project convention:\s*(.+)$")),
    ("key-decisions", re.compile(r"(?i)^Decision:\s*(.+)$")),
    ("dependency-facts", re.compile(r"(?i)^Dependency:\s*(.+)$")),
    ("user-preferences", re.compile(r"(?i)^Preference:\s*(.+)$")),
    ("project-conventions", re.compile(r"^项目约定：\s*(.+)$")),
    ("key-decisions", re.compile(r"^决策：\s*(.+)$")),
    ("dependency-facts", re.compile(r"^依赖：\s*(.+)$")),
    ("user-preferences", re.compile(r"^偏好：\s*(.+)$")),
)
SECRET_SHAPED_TEXT_PATTERN = re.compile(
    r"(?i)(\b(api[_ -]?key|token|secret|password)\b|sk-[A-Za-z0-9_-]{6,})"
)
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class PromptPrefix:
    # prefix 除了文本本身，还带一小份元数据，
    # 这样 runtime 才能明确判断 prefix 是否可以复用。
    text: str
    hash: str
    workspace_fingerprint: str
    tool_signature: str
    built_at: str


class FinalAnswerDeltaFilter:
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
                self.buffer = self.buffer[-(len(self.start_tag) - 1) :]
                return
            if start == -1:
                self.buffer = self.buffer[-(len(self.start_tag) - 1) :]
                return
            self.buffer = self.buffer[start + len(self.start_tag) :]
            self.in_final = True

        while self.in_final and self.buffer:
            end = self.buffer.find(self.end_tag)
            if end >= 0:
                if end > 0:
                    self.on_text(self.buffer[:end])
                self.buffer = self.buffer[end + len(self.end_tag) :]
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


class SessionStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id):
        return self.root / f"{session_id}.json"

    def save(self, session):
        path = self.path(session["id"])
        path.write_text(json.dumps(session, indent=2), encoding="utf-8")
        return path

    def load(self, session_id):
        return json.loads(self.path(session_id).read_text(encoding="utf-8"))

    def latest(self):
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        return files[-1].stem if files else None


class Pico:
    def __init__(
        self,
        model_client,
        workspace,
        session_store,
        session=None,
        run_store=None,
        approval_policy="ask",
        max_steps=None,
        max_new_tokens=512,
        depth=0,
        max_depth=1,
        read_only=False,
        shell_env_allowlist=None,
        secret_env_names=None,
        feature_flags=None,
        approval_handler=None,
        event_handler=None,
        context_window=None,
        safety_margin_tokens=256,
        allowed_tools=None,
        requires_workspace_change=False,
        runtime_mode=None,
        emergency_cap=None,
    ):
        self.model_client = model_client
        self.workspace = workspace
        self.root = Path(workspace.repo_root)
        self.session_store = session_store
        self.approval_policy = approval_policy
        # max_steps=None 表示“不设固定步数预算”：interactive 模式由
        # Progress Watchdog + emergency cap 决定何时停止；experiment 模式
        # 由 ExperimentRunner 显式传入 task.step_budget。
        self.max_steps = max_steps
        self.runtime_mode = (
            str(runtime_mode or RUNTIME_MODE_INTERACTIVE).strip()
            or RUNTIME_MODE_INTERACTIVE
        )
        self.emergency_cap = (
            int(emergency_cap)
            if emergency_cap is not None
            else DEFAULT_INTERACTIVE_EMERGENCY_CAP
        )
        self.max_new_tokens = max_new_tokens
        self.context_window = context_window
        self.safety_margin_tokens = int(safety_margin_tokens)
        self.token_counter = getattr(
            model_client, "token_counter", None
        ) or resolve_token_counter(getattr(model_client, "model", ""))
        self.depth = depth
        self.max_depth = max_depth
        self.read_only = read_only
        self.allowed_tools = None if allowed_tools is None else frozenset(allowed_tools)
        self.requires_workspace_change = bool(requires_workspace_change)
        self.shell_env_allowlist = tuple(
            shell_env_allowlist or DEFAULT_SHELL_ENV_ALLOWLIST
        )
        self.secret_env_names = {str(name).upper() for name in (secret_env_names or ())}
        self.approval_handler = approval_handler
        self.event_handler = event_handler
        self.cancel_checker = None
        self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        if feature_flags:
            self.feature_flags.update(
                {str(key): bool(value) for key, value in feature_flags.items()}
            )
        self.run_store = run_store or RunStore(
            Path(workspace.repo_root) / ".codecub" / "runs"
        )
        self.session = session or {
            "id": datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            "created_at": now(),
            "workspace_root": workspace.repo_root,
            "history": [],
            "memory": memorylib.default_memory_state(),
        }
        self._ensure_session_shape()
        self.memory = memorylib.LayeredMemory(
            self.session.setdefault("memory", memorylib.default_memory_state()),
            workspace_root=self.root,
        )
        self.session["memory"] = self.memory.to_dict()
        # Phase 3: Memory 2.0（Evidence Store + Durable Memory + Retriever）。
        # 与 legacy LayeredMemory 并存：v2 是新的记忆体系，v1 保留为兼容适配器。
        self.memory_v2 = MemoryV2(
            self.root,
            token_counter=self.token_counter,
            trace=self.memory_v2_trace,
        )
        self.memory_v2.migrate_legacy(self.session.get("memory"))
        self.code_index = CodeIndex(self.root)
        self.code_index.refresh()
        self.tools = self.build_tools()
        self.prefix_state = self.build_prefix()
        self.prefix = self.prefix_state.text
        self.context_manager = ContextManager(self)
        # Phase 2: Context Compiler（task-local Working State + 分层 Context）。
        self.working_state = WorkingState()
        self.context_compiler = self.build_context_compiler()
        self.resume_state = self.evaluate_resume_state()
        self.session_path = self.session_store.save(self.session)
        self.current_task_state = None
        self.current_run_dir = None
        self.last_prompt_metadata = {}
        self.last_completion_metadata = {}
        self.current_run_usage = []
        self.current_run_source_reads = []
        self.current_planning = {}
        self.watchdog = ProgressWatchdog(file_hash_fn=self._file_freshness)
        self.edit_decision_watchdog = EditDecisionWatchdog(
            file_hash_fn=self._file_freshness
        )
        self.usage_store = UsageStore(self.root / ".codecub" / "usage")
        self.last_model_error = {}
        self.last_durable_promotions = []
        self.last_durable_rejections = []
        self.last_durable_superseded = []
        self._last_tool_result_metadata = {}
        self._last_prefix_refresh = {
            "workspace_changed": False,
            "prefix_changed": False,
        }
        # Phase 3: Memory 2.0 run-local state。
        self._current_memory_result = None
        self._memory_retrieval_signature = ""
        self.last_memory_v2_promotions = []
        self.last_memory_v2_rejections = []
        self.last_memory_v2_superseded = []
        self.last_memory_v2_conflicts = []

    @classmethod
    def from_session(cls, model_client, workspace, session_store, session_id, **kwargs):
        return cls(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            session=session_store.load(session_id),
            **kwargs,
        )

    def _ensure_session_shape(self):
        self.session.setdefault("history", [])
        self.session.setdefault("memory", memorylib.default_memory_state())
        checkpoints = self.session.setdefault("checkpoints", {})
        if not isinstance(checkpoints, dict):
            checkpoints = {}
            self.session["checkpoints"] = checkpoints
        checkpoints.setdefault("current_id", "")
        checkpoints.setdefault("items", {})
        runtime_identity = self.session.setdefault("runtime_identity", {})
        if not isinstance(runtime_identity, dict):
            self.session["runtime_identity"] = {}
        resume_state = self.session.setdefault("resume_state", {})
        if not isinstance(resume_state, dict):
            self.session["resume_state"] = {}

    def current_runtime_identity(self):
        return {
            "session_id": self.session.get("id", ""),
            "cwd": str(self.root),
            "model": str(getattr(self.model_client, "model", "")),
            "model_client": self.model_client.__class__.__name__,
            "approval_policy": self.approval_policy,
            "read_only": bool(self.read_only),
            "max_steps": self.max_steps,
            "runtime_mode": self.runtime_mode,
            "emergency_cap": int(self.emergency_cap or 0),
            "max_new_tokens": int(self.max_new_tokens),
            "feature_flags": dict(self.feature_flags),
            "shell_env_allowlist": list(self.shell_env_allowlist),
            "workspace_fingerprint": getattr(
                getattr(self, "prefix_state", None),
                "workspace_fingerprint",
                self.workspace.fingerprint(),
            ),
            "tool_signature": self.tool_signature(),
        }

    @property
    def effective_step_budget(self):
        """当前 run 的固定步数预算；interactive unlimited 时为 None。"""
        if self.max_steps is not None:
            return int(self.max_steps)
        if self.runtime_mode == RUNTIME_MODE_EXPERIMENT:
            # experiment 必须保留固定预算语义，防止配置漂移成 unlimited。
            return DEFAULT_MAX_STEPS
        return None

    def checkpoint_state(self):
        self._ensure_session_shape()
        return self.session["checkpoints"]

    def current_checkpoint(self):
        state = self.checkpoint_state()
        checkpoint_id = str(state.get("current_id", "")).strip()
        if not checkpoint_id:
            return None
        return state.get("items", {}).get(checkpoint_id)

    def invalidate_stale_memory(self):
        invalidated = self.memory.invalidate_stale_file_summaries()
        self.session["memory"] = self.memory.to_dict()
        return invalidated

    def evaluate_resume_state(self):
        previous_resume_state = dict(self.session.get("resume_state", {}) or {})
        invalidated = self.invalidate_stale_memory()
        # Phase 3: Evidence freshness 对照 live workspace 重算（stale/missing）。
        if self.memory_v2_enabled():
            try:
                self.memory_v2.refresh_freshness()
            except Exception:
                pass
        checkpoint = self.current_checkpoint()
        status = CHECKPOINT_NONE_STATUS
        stale_paths = list(invalidated)
        mismatch_fields = []
        if checkpoint:
            if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                status = CHECKPOINT_SCHEMA_MISMATCH_STATUS
            else:
                for item in checkpoint.get("key_files", []):
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue
                    expected = item.get("freshness")
                    current = memorylib.file_freshness(path, self.root)
                    if expected != current and path not in stale_paths:
                        stale_paths.append(path)
                saved_identity = dict(
                    checkpoint.get("runtime_identity", {})
                    or self.session.get("runtime_identity", {})
                    or {}
                )
                current_identity = self.current_runtime_identity()
                identity_keys = (
                    "cwd",
                    "model",
                    "model_client",
                    "approval_policy",
                    "read_only",
                    "max_steps",
                    "runtime_mode",
                    "emergency_cap",
                    "max_new_tokens",
                    "feature_flags",
                    "shell_env_allowlist",
                    "workspace_fingerprint",
                    "tool_signature",
                )
                for key in identity_keys:
                    if key not in saved_identity:
                        continue
                    if saved_identity.get(key) != current_identity.get(key):
                        mismatch_fields.append(key)
                mismatch_fields.sort()
                if stale_paths:
                    status = CHECKPOINT_PARTIAL_STALE_STATUS
                elif mismatch_fields:
                    status = CHECKPOINT_WORKSPACE_MISMATCH_STATUS
                else:
                    status = CHECKPOINT_FULL_VALID_STATUS

        resume_state = {
            "status": status,
            "stale_paths": stale_paths,
            "runtime_identity_mismatch_fields": mismatch_fields,
            "stale_summary_invalidations": max(
                len(invalidated),
                int(previous_resume_state.get("stale_summary_invalidations", 0))
                if status == CHECKPOINT_PARTIAL_STALE_STATUS
                else 0,
            ),
        }
        self.session["resume_state"] = resume_state
        self.session["runtime_identity"] = self.current_runtime_identity()
        return resume_state

    def render_checkpoint_text(self):
        checkpoint = self.current_checkpoint()
        if not checkpoint:
            return ""
        lines = [
            "Task checkpoint:",
            f"- Resume status: {self.resume_state.get('status', CHECKPOINT_NONE_STATUS)}",
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
            lines.append(
                "- Completed: "
                + " | ".join(str(item) for item in checkpoint.get("completed", []))
            )
        if checkpoint.get("excluded"):
            lines.append(
                "- Excluded: "
                + " | ".join(str(item) for item in checkpoint.get("excluded", []))
            )
        if self.resume_state.get("stale_paths"):
            lines.append(
                "- Stale paths: " + ", ".join(self.resume_state["stale_paths"])
            )
        summary = str(checkpoint.get("summary", "")).strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        return "\n".join(lines)

    @staticmethod
    def remember(bucket, item, limit):
        if not item:
            return
        if item in bucket:
            bucket.remove(item)
        bucket.append(item)
        del bucket[:-limit]

    def _file_freshness(self, path):
        """文件内容 hash（供 Watchdog / EditDecisionWatchdog 做 stale->fresh 判定）。"""
        try:
            return memorylib.file_freshness(path, self.root)
        except Exception:
            return None

    def build_tools(self):
        tools = toolkit.build_tool_registry(self)
        if self.allowed_tools is None:
            return tools
        return {
            name: tool for name, tool in tools.items() if name in self.allowed_tools
        }

    def build_context_compiler(self):
        """Phase 2: 装配 Context Compiler。

        feature flag `context_compiler` 关闭时返回 None，走旧 ContextManager。
        Condenser 默认 deterministic（不消费主模型 outputs），保证确定性测试
        稳定；真实 probe 阶段可注入 LLM condenser。
        """
        if not self.feature_enabled("context_compiler"):
            return None
        budget = ContextBudget.resolve(
            context_window=self.context_window,
            max_new_tokens=self.max_new_tokens,
            safety_margin_tokens=self.safety_margin_tokens,
        )
        condenser = HistoryCondenser(
            model_client=None,
            redact_fn=self.redact_text,
            token_counter=self.token_counter,
        )
        return ContextCompiler(
            token_counter=self.token_counter,
            budget=budget,
            condenser=condenser,
            code_index=self.code_index,
            redact_fn=self.redact_text,
            workspace_root=self.root,
        )

    def _pinned_extra(self, user_message=None):
        extras = {
            PINNED_PROJECT_RULES: self.prefix,
            PINNED_SAFETY: (
                f"Approval policy: {self.approval_policy}; read_only: {self.read_only}"
            ),
            PINNED_RUNTIME_MODE: (
                f"runtime_mode: {self.runtime_mode}; "
                f"effective_step_budget: {self.effective_step_budget}; "
                f"emergency_cap: {self.emergency_cap}"
            ),
        }
        ledger_text = self.evidence_ledger_text()
        if ledger_text:
            extras["pinned:evidence-ledger"] = ledger_text
        try:
            checkpoint_text = str(self.render_checkpoint_text() or "").strip()
        except Exception:
            checkpoint_text = ""
        if checkpoint_text:
            extras["pinned:checkpoint"] = checkpoint_text
        # Phase 3: memory_v2 ON 时检索走 Context Compiler 的 bounded memory layer，
        # 这里不再注入 v1 relevant-memory，避免两套记忆同时进 Prompt。
        if (
            not self.memory_v2_enabled()
            and self.feature_enabled("memory")
            and self.feature_enabled("relevant_memory")
        ):
            try:
                notes = self.memory.retrieval_candidates(
                    str(user_message or ""), limit=3
                )
            except Exception:
                notes = []
            if notes:
                lines = ["Relevant memory:"]
                lines.extend(f"- {str(note.get('text', ''))}" for note in notes)
                extras["pinned:relevant-memory"] = "\n".join(lines)
        return extras

    def tool_signature(self):
        payload = []
        for name in sorted(self.tools):
            tool = self.tools[name]
            payload.append(
                {
                    "name": name,
                    "schema": tool["schema"],
                    "risky": tool["risky"],
                    "description": tool["description"],
                }
            )
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def build_prefix(self):
        tool_lines = []
        for name, tool in self.tools.items():
            fields = ", ".join(
                f"{key}: {value}" for key, value in tool["schema"].items()
            )
            risk = "approval required" if tool["risky"] else "safe"
            tool_lines.append(f"- {name}({fields}) [{risk}] {tool['description']}")
        tool_text = "\n".join(tool_lines)
        xml_tool_rules = ""
        if "write_file" in self.tools:
            xml_tool_rules += (
                "- For write_file calls with multi-line content, prefer XML style:\n"
                '  <tool name="write_file" path="file.py"><content>...</content></tool>\n'
            )
        if "patch_file" in self.tools:
            xml_tool_rules += (
                "- For patch_file calls with multi-line text, use <old_text> and <new_text>:\n"
                '  <tool name="patch_file" path="file.py"><old_text>...</old_text><new_text>...</new_text></tool>'
            )
        examples = "\n".join(
            [
                toolkit.tool_example(name)
                for name in self.tools
                if toolkit.tool_example(name)
            ]
            + ["<final>Done.</final>"]
        )
        task_contract = (
            "- This task requires an actual workspace modification. Analysis, repository inspection, and test execution alone do not complete it.\n"
            "- Once you have identified a plausible minimal fix, use an allowed editing tool to make the smallest justified change, then verify it.\n"
            "- Do not continue broad exploration after you have enough evidence to make a specific edit."
            if self.requires_workspace_change
            else ""
        )
        # prefix 可以理解成 agent 的“工作手册”：
        # 它是谁、工具怎么调用、当前仓库是什么状态，都写在这里。
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

            {self.workspace.text()}
            """
        ).strip()
        return PromptPrefix(
            text=text,
            hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            workspace_fingerprint=self.workspace.fingerprint(),
            tool_signature=self.tool_signature(),
            built_at=now(),
        )

    def _apply_prefix_state(self, prefix_state):
        self.prefix_state = prefix_state
        self.prefix = prefix_state.text

    def refresh_prefix(self, force=False):
        previous_hash = getattr(getattr(self, "prefix_state", None), "hash", None)
        previous_workspace_fingerprint = getattr(
            getattr(self, "prefix_state", None), "workspace_fingerprint", None
        )

        # 工作区事实相对稳定，所以这里按整体刷新；
        # 只有这些事实真的变化了，才重建完整 prefix。
        refreshed_workspace = WorkspaceContext.build(self.root)
        refreshed_workspace_fingerprint = refreshed_workspace.fingerprint()
        workspace_changed = (
            force or refreshed_workspace_fingerprint != previous_workspace_fingerprint
        )
        if workspace_changed:
            self.workspace = refreshed_workspace

        prefix_state = (
            self.build_prefix()
            if workspace_changed or force or previous_hash is None
            else self.prefix_state
        )
        prefix_changed = force or previous_hash != prefix_state.hash
        if prefix_changed:
            self._apply_prefix_state(prefix_state)

        self._last_prefix_refresh = {
            "workspace_changed": workspace_changed,
            "prefix_changed": prefix_changed,
        }
        return dict(self._last_prefix_refresh)

    def memory_text(self):
        evidence = self.evidence_ledger_text()
        memory = self.memory.render_memory_text()
        readiness = self.action_readiness_text()
        return "\n".join(part for part in (readiness, evidence, memory) if part)

    def action_readiness_text(self):
        if not self.requires_workspace_change:
            return ""
        if self.current_planning.get("action_readiness") != "action_expected":
            return ""
        return (
            "Action readiness: you have inspected relevant source evidence. "
            "If you can identify a specific minimal fix, make the edit before performing more broad exploration."
        )

    def evidence_ledger_text(self):
        entries = self.current_planning.get("evidence_ledger", [])
        if not entries:
            return ""
        lines = ["Inspected source evidence (current workspace revision):"]
        for entry in entries:
            lines.append(
                f"- {entry['path']} lines {entry['start']}-{entry['end']} [{entry['marker']}]: {entry['hint']}"
            )
        return "\n".join(lines)

    def memory_recall_debug_text(self, query):
        if self.memory_v2_enabled():
            try:
                result = self.memory_v2.retrieve(query, force=True)
                lines = ["Memory 2.0 retrieval debug:"]
                lines.append(f"query: {result.query[:200]}")
                lines.append(
                    f"budget: evidence_top_k={result.evidence_top_k} "
                    f"durable_top_k={result.durable_top_k} "
                    f"token_budget={result.token_budget}"
                )
                lines.append(f"stale_count: {result.stale_count}")
                lines.append(f"missing_count: {result.missing_count}")
                lines.append("Selected:")
                if not result.items:
                    lines.append("- none")
                for index, item in enumerate(result.items, start=1):
                    lines.append(f"{index}. [{item.marker}] {item.text}")
                    lines.append(
                        f"   kind: {item.kind}  score: {item.score:.1f}  reason: {item.reason}"
                    )
                return "\n".join(lines)
            except Exception:
                pass
        return self.memory.retrieval_debug_view(query)

    def history_text(self):
        history = self.session["history"]
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
                lines.append(
                    f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}"
                )
                lines.append(clip(item["content"], limit))
            else:
                limit = 900 if recent else 220
                lines.append(f"[{item['role']}] {clip(item['content'], limit)}")

        return clip("\n".join(lines), MAX_HISTORY)

    def feature_enabled(self, name):
        return bool(self.feature_flags.get(str(name), False))

    # ------------------------------------------------------------------
    # Phase 3: Memory 2.0 helpers
    # ------------------------------------------------------------------

    def memory_v2_enabled(self):
        """Memory 2.0 master switch；`memory=False` 仍是完全 Memory OFF。"""
        return bool(
            self.feature_enabled("memory") and self.feature_enabled("memory_v2")
        )

    def memory_v2_trace(self, event, payload=None):
        """MemoryV2 的 trace 回调；无当前 run 时静默跳过。"""
        task_state = getattr(self, "current_task_state", None)
        if task_state is not None:
            self.emit_trace(task_state, event, payload or {})

    def _memory_signature(self):
        ws = self.working_state or WorkingState()
        blockers = "|".join(
            str(item.get("text", "")) for item in (ws.blockers or [])
        )
        symbols = "|".join(
            f"{item.get('path', '')}:{item.get('name', '')}"
            for item in (ws.relevant_symbols or [])
        )
        files = "|".join(str(path) for path in (ws.changed_files or []))
        return f"{blockers}||{symbols}||{files}"

    def _refresh_memory_retrieval(self, user_message, force=False):
        """Retrieval trigger：task start / blocker 变化 / recovery turn。"""
        if not self.memory_v2_enabled():
            self._current_memory_result = None
            return None
        try:
            result = self.memory_v2.retrieve(
                user_message, self.working_state, force=force
            )
        except Exception:
            result = None
        self._current_memory_result = result
        self._memory_retrieval_signature = self._memory_signature()
        return result

    def _memory_layer(self):
        """Context Compiler 的 bounded memory layer（文本 + 元数据）。"""
        if not self.memory_v2_enabled():
            return "", {}
        result = getattr(self, "_current_memory_result", None)
        if result is None or not result.items:
            return "", {}
        return result.render(), {
            "evidence_count": len(result.evidence_items),
            "durable_count": len(result.durable_items),
            "stale_count": result.stale_count,
            "token_budget": result.token_budget,
        }

    def record_memory_v2_evidence(self, name, args, result):
        """客观工具事件 → Evidence Store（read/symbol/outline/references/verification）。"""
        if not self.memory_v2_enabled() or not self.feature_enabled(
            "evidence_memory"
        ):
            return []
        try:
            created = self.memory_v2.record_tool_evidence(
                name,
                args,
                result,
                metadata=self._last_tool_result_metadata,
            )
            if name == "read_file":
                self.memory_v2.note_read(str(args.get("path") or ""))
            return created
        except Exception:
            return []

    def extract_memory_v2(self, user_message, final_answer):
        """Run 结束：Extraction → Consolidation → Persist（Memory 2.0 管线）。"""
        if not self.memory_v2_enabled() or not self.feature_enabled("durable_memory"):
            return None
        run_id = (
            self.current_task_state.run_id if self.current_task_state is not None else ""
        )
        promoted, rejections, superseded, conflicts, _duplicates = (
            self.memory_v2.extract_and_persist(
                self.working_state, user_message, final_answer, run_id=run_id
            )
        )
        self.last_memory_v2_promotions = list(promoted)
        self.last_memory_v2_rejections = list(rejections)
        self.last_memory_v2_superseded = list(superseded)
        self.last_memory_v2_conflicts = list(conflicts)
        return promoted, rejections, superseded, conflicts

    def prompt(self, user_message):
        prompt, _ = self._build_prompt_and_metadata(user_message)
        return prompt

    def record(self, item):
        self.session["history"].append(item)
        self.session_path = self.session_store.save(self.session)

    @staticmethod
    def looks_sensitive_env_name(name):
        upper = str(name).upper()
        return any(
            upper == marker or upper.endswith(marker) or upper.endswith(f"_{marker}")
            for marker in SENSITIVE_ENV_NAME_MARKERS
        )

    def is_secret_env_name(self, name):
        upper = str(name).upper()
        return upper in self.secret_env_names or self.looks_sensitive_env_name(upper)

    def configured_secret_env_items(self):
        items = [
            (name, value)
            for name, value in os.environ.items()
            if str(name).upper() in self.secret_env_names and value
        ]
        items.sort(key=lambda item: item[0])
        return items

    def detected_secret_env_items(self):
        items = [
            (name, value)
            for name, value in os.environ.items()
            if self.is_secret_env_name(name) and value
        ]
        items.sort(key=lambda item: item[0])
        return items

    def secret_env_summary(self):
        names = [name for name, _ in self.configured_secret_env_items()]
        return {
            "secret_env_count": len(names),
            "secret_env_names": names,
        }

    def detected_secret_env_summary(self):
        names = [name for name, _ in self.detected_secret_env_items()]
        return {
            "secret_env_count": len(names),
            "secret_env_names": names,
        }

    def redact_text(self, text):
        text = str(text)
        for _, value in sorted(
            self.detected_secret_env_items(),
            key=lambda item: len(item[1]),
            reverse=True,
        ):
            text = text.replace(value, REDACTED_VALUE)
        return text

    def redact_artifact(self, value, key=None):
        if key and self.is_secret_env_name(key):
            return REDACTED_VALUE
        if isinstance(value, dict):
            return {
                str(item_key): self.redact_artifact(item_value, key=item_key)
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [self.redact_artifact(item, key=key) for item in value]
        if isinstance(value, tuple):
            return [self.redact_artifact(item, key=key) for item in value]
        if isinstance(value, str):
            redacted = self.redact_text(value)
            return redacted
        return value

    def shell_env(self):
        env = {
            name: os.environ[name]
            for name in self.shell_env_allowlist
            if name in os.environ
        }
        env["PWD"] = str(self.root)
        # Experiment workspaces can be nested under the repository's artifact
        # directory while intentionally excluding .git.  On Windows Git does
        # not reliably honor GIT_CEILING_DIRECTORIES for drive-qualified paths,
        # so make Git operate on the platform null device instead of discovering
        # the parent source repository.
        env["GIT_DIR"] = os.devnull
        if "PATH" not in env and os.environ.get("PATH"):
            env["PATH"] = os.environ["PATH"]
        if os.name == "nt":
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            env.setdefault("SystemRoot", system_root)
            env.setdefault(
                "ComSpec",
                os.environ.get(
                    "ComSpec", str(Path(system_root) / "System32" / "cmd.exe")
                ),
            )
        return env

    def prompt_metadata(self, user_message, prompt):
        _, metadata = self._build_prompt_and_metadata(user_message)
        return metadata

    def _add_legacy_metadata_compat(self, metadata, prompt, user_message):
        """Context Compiler 产出兼容旧 ContextManager 的 metadata 字段。

        旧 metrics / read_guard / 实验分析脚本依赖这些字段；字段语义与
        旧实现保持一致，避免破坏 Phase 1 及更早的分析链路。
        """
        unit = "tokens" if self.token_counter is not None else "chars"
        estimated = metadata.get("compiled_context_tokens") or 0
        evidence_entries = []
        if hasattr(self, "evidence_ledger_entries"):
            for entry in self.evidence_ledger_entries():
                marker = str(entry.get("marker", ""))
                evidence_entries.append(
                    {
                        "path": str(entry.get("path", "")),
                        "start": int(entry.get("start", 1)),
                        "end": int(entry.get("end", 200)),
                        "freshness": str(entry.get("freshness", "")),
                        "last_read_step": entry.get("last_read_step"),
                        "visible": bool(marker and marker in prompt),
                    }
                )
        # Phase 3: memory_v2 ON 时，legacy relevant_memory 兼容块反映 v2 检索结果，
        # 保持旧 metrics 脚本（selected_count / rendered_notes 等）可继续读取。
        if self.memory_v2_enabled() and getattr(self, "_current_memory_result", None):
            result = self._current_memory_result
            rendered_notes = []
            for item in result.items:
                rendered_notes.append(
                    {
                        "text": item.text,
                        "kind": item.kind,
                        "marker": item.marker,
                        "source": item.path or item.topic or "",
                        "reason": item.reason,
                        "score": item.score,
                        "status": item.status,
                    }
                )
            metadata["relevant_memory"] = {
                "limit": result.evidence_top_k + result.durable_top_k,
                "selected_count": len(result.items),
                "selected_notes": [item.text for item in result.items],
                "selected_sources": [
                    item.path or item.topic for item in result.items
                ],
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
        metadata.update(
            {
                "prompt_chars": len(prompt),
                "prompt_budget_chars": self.context_manager.total_budget,
                "prompt_over_budget": False,
                "budget_mode": "token" if self.token_counter is not None else "char",
                "budget_unit": unit,
                "estimated_prompt_tokens": estimated,
                "prompt_tokens": estimated,
                "section_order": [
                    "prefix",
                    "memory",
                    "relevant_memory",
                    "history",
                    "current_request",
                ],
                "section_budgets": {
                    "prefix": None,
                    "memory": None,
                    "relevant_memory": None,
                    "history": None,
                    "current_request": None,
                },
                "sections": {
                    "pinned": {
                        "raw_chars": 0,
                        "budget_chars": None,
                        "rendered_chars": metadata.get("pinned_tokens", 0),
                    },
                    "working_state": {
                        "raw_chars": 0,
                        "budget_chars": None,
                        "rendered_chars": metadata.get("working_state_tokens", 0),
                    },
                    "recent_verbatim": {
                        "raw_chars": 0,
                        "budget_chars": None,
                        "rendered_chars": metadata.get("recent_verbatim_tokens", 0),
                    },
                    "compressed_history": {
                        "raw_chars": 0,
                        "budget_chars": None,
                        "rendered_chars": metadata.get("compressed_history_tokens", 0),
                    },
                },
                "inspected_evidence": {
                    "entry_count": len(evidence_entries),
                    "visible_entry_count": sum(
                        1 for entry in evidence_entries if entry["visible"]
                    ),
                    "entries": evidence_entries,
                },
                "budget_reductions": [],
                "reduction_order": [],
                "relevant_memory": {
                    "limit": 3,
                    "selected_count": 0,
                    "selected_notes": [],
                    "selected_sources": [],
                    "selected_kinds": [],
                    "selected_reasons": [],
                    "selected_scores": [],
                    "selected_matches": [],
                    "selected_durable_count": 0,
                    "raw_chars": 0,
                    "rendered_chars": 0,
                    "rendered_notes": [],
                    "rendered_count": 0,
                },
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
            }
        )
        return metadata

    def _build_prompt_and_metadata(self, user_message, status_callback=None, task_state=None):
        if status_callback is not None:
            status_callback(
                "checking_workspace", "Checking repository state", str(self.root)
            )
        refresh = self.refresh_prefix()
        if status_callback is not None:
            status_callback("loading_memory", "Loading session memory", "")
        self.resume_state = self.evaluate_resume_state()
        if status_callback is not None:
            status_callback("building_prompt", "Building prompt", "")
        # Phase 2: Interactive/默认路径走 Context Compiler（Pinned + Working State +
        # Recent Verbatim + Compressed History + Repo Map）；旧 ContextManager 作为
        # legacy adapter（feature flag 关闭时使用）。
        if self.context_compiler is not None:
            if task_state is not None:
                self.emit_trace(
                    task_state,
                    "context_compile_started",
                    {
                        "step": task_state.tool_steps,
                        "history_entries": len(self.session.get("history", [])),
                        "working_state_facts": len(self.working_state.known_facts),
                    },
                )
            self.working_state.refresh_fact_freshness(self.root)
            stale_count = len(self.working_state.stale_facts())
            memory_layer, memory_meta = self._memory_layer()
            prompt, metadata = self.context_compiler.compile_text(
                user_message,
                working_state=self.working_state,
                history=self.session.get("history", []),
                pinned_extra=self._pinned_extra(user_message),
                memory_layer=memory_layer,
                memory_meta=memory_meta,
            )
            self._add_legacy_metadata_compat(metadata, prompt, user_message)
            if task_state is not None:
                if stale_count:
                    self.emit_trace(
                        task_state,
                        "context_fact_stale",
                        {"stale_fact_count": stale_count, "step": task_state.tool_steps},
                    )
                if metadata.get("should_compress"):
                    self.emit_trace(
                        task_state,
                        "compression_triggered",
                        {
                            "mode": "legacy",
                            "estimated_tokens": metadata.get(
                                "candidate_context_tokens"
                            ),
                            "usable_input_budget": metadata.get("usable_input_budget"),
                            "compression_count": metadata.get("compression_count"),
                        },
                    )
                    self.emit_trace(
                        task_state,
                        "compression_started",
                        {"mode": "legacy"},
                    )
                    self.emit_trace(
                        task_state,
                        "history_span_compacted",
                        {
                            "raw_history_tokens": metadata.get(
                                "raw_history_tokens"
                            ),
                            "compressed_history_tokens": metadata.get(
                                "compressed_history_tokens"
                            ),
                        },
                    )
                if metadata.get("repo_map_selection", {}).get("selected_files"):
                    self.emit_trace(
                        task_state,
                        "repo_map_selected",
                        {
                            "selected_files": metadata["repo_map_selection"][
                                "selected_files"
                            ],
                            "estimated_tokens": metadata["repo_map_selection"].get(
                                "estimated_tokens"
                            ),
                        },
                    )
                self.emit_trace(
                    task_state,
                    "context_compile_finished",
                    {
                        "compilation_metadata": metadata,
                    },
                )
        else:
            prompt, metadata = self.context_manager.build(user_message)
        available_prompt_tokens = resolve_prompt_budget(
            self.context_window, self.max_new_tokens, self.safety_margin_tokens
        )
        # 这里把“这轮 prompt 是怎么拼出来的”连同缓存相关状态一起记下来，
        # 后面 trace/report 才能解释清楚：为什么这一轮 prefix 变了、缓存有没有命中。
        metadata.update(
            {
                "prefix_chars": len(self.prefix),
                "workspace_chars": len(self.workspace.text()),
                "memory_chars": len(self.memory_text()),
                "history_chars": len(self.history_text()),
                "request_chars": len(user_message),
                "tool_count": len(self.tools),
                "workspace_docs": len(self.workspace.project_docs),
                "recent_commits": len(self.workspace.recent_commits),
                "prefix_hash": self.prefix_state.hash,
                "prompt_cache_key": self.prefix_state.hash,
                "workspace_fingerprint": self.prefix_state.workspace_fingerprint,
                "tool_signature": self.prefix_state.tool_signature,
                "workspace_changed": refresh["workspace_changed"],
                "prefix_changed": refresh["prefix_changed"],
                "prompt_cache_supported": bool(
                    getattr(self.model_client, "supports_prompt_cache", False)
                ),
                "resume_status": self.resume_state.get(
                    "status", CHECKPOINT_NONE_STATUS
                ),
                "stale_summary_invalidations": int(
                    self.resume_state.get("stale_summary_invalidations", 0)
                ),
                "stale_paths": list(self.resume_state.get("stale_paths", [])),
                "runtime_identity_mismatch_fields": list(
                    self.resume_state.get("runtime_identity_mismatch_fields", [])
                ),
                "context_window": self.context_window,
                "max_new_tokens": int(self.max_new_tokens),
                "safety_margin_tokens": self.safety_margin_tokens,
                "available_prompt_tokens": available_prompt_tokens,
                "token_counter_source": getattr(
                    self.token_counter, "source", "unavailable"
                ),
                "token_counter_quality": getattr(
                    self.token_counter, "quality", "unavailable"
                ),
            }
        )
        metadata.update(self.detected_secret_env_summary())
        return prompt, metadata

    def emit_trace(self, task_state, event, payload=None):
        payload = self.redact_artifact(payload or {})
        payload["event"] = event
        payload["created_at"] = now()
        # trace 是运行中的逐事件时间线，适合回答“这一轮 agent 到底做了什么”。
        self.run_store.append_trace(task_state, payload)
        return payload

    def emit_app_event(self, event_name, task_state, payload=None):
        if self.event_handler is not None:
            self.event_handler(event_name, dict(payload or {}), self, task_state)

    def emit_run_status(
        self, task_state, phase, label, detail="", started_at="", run_started_at=None
    ):
        elapsed_ms = (
            int((time.monotonic() - run_started_at) * 1000)
            if run_started_at is not None
            else 0
        )
        self.emit_app_event(
            "run_status",
            task_state,
            {
                "phase": phase,
                "label": label,
                "detail": detail,
                "started_at": started_at,
                "elapsed_ms": elapsed_ms,
            },
        )

    def capture_workspace_snapshot(self):
        snapshot = {}
        for path in self.root.rglob("*"):
            try:
                relative_parts = path.relative_to(self.root).parts
            except ValueError:
                continue
            if any(part in IGNORED_PATH_NAMES for part in relative_parts):
                continue
            if not path.is_file():
                continue
            try:
                snapshot[path.relative_to(self.root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except Exception:
                continue
        return snapshot

    @staticmethod
    def diff_workspace_snapshots(before, after):
        changed_paths = []
        summaries = []
        all_paths = sorted(set(before) | set(after))
        for path in all_paths:
            if before.get(path) == after.get(path):
                continue
            changed_paths.append(path)
            if path not in before:
                summaries.append(f"created:{path}")
            elif path not in after:
                summaries.append(f"deleted:{path}")
            else:
                summaries.append(f"modified:{path}")
        return changed_paths, summaries

    def create_checkpoint(self, task_state, user_message, trigger):
        state = self.checkpoint_state()
        current = self.current_checkpoint()
        checkpoint_id = "ckpt_" + uuid.uuid4().hex[:8]
        key_files = []
        freshness = {}
        for path in self.memory.to_dict()["working"]["recent_files"]:
            file_freshness = memorylib.file_freshness(path, self.root)
            freshness[path] = file_freshness
            key_files.append({"path": path, "freshness": file_freshness})
        # Phase 3: Working State（权威 task-local 真相）+ Evidence Store 路径
        # 也进入 checkpoint key_files，保证 resume 时能检测源码漂移。
        if self.memory_v2_enabled():
            ws_paths = []
            ws_paths.extend(str(p) for p in (self.working_state.changed_files or []))
            for item in (self.working_state.relevant_symbols or []):
                path = str(item.get("path", "") or "")
                if path and path not in ws_paths:
                    ws_paths.append(path)
            for record in self.memory_v2.evidence_store.latest_records():
                path = str(record.get("path", "") or "")
                if path and path not in ws_paths:
                    ws_paths.append(path)
            for path in ws_paths:
                if any(item["path"] == path for item in key_files):
                    continue
                file_freshness = memorylib.file_freshness(path, self.root)
                freshness[path] = file_freshness
                key_files.append({"path": path, "freshness": file_freshness})
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": current.get("checkpoint_id", "") if current else "",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "created_at": now(),
            "current_goal": str(user_message),
            "completed": [task_state.final_answer] if task_state.final_answer else [],
            "excluded": [],
            "current_blocker": ""
            if str(task_state.stop_reason or "") in ("", "final_answer_returned")
            else str(task_state.stop_reason),
            "next_step": self.infer_next_step(task_state),
            "key_files": key_files,
            "freshness": freshness,
            "summary": f"{trigger}: {clip(str(user_message), 120)}",
            "runtime_identity": self.current_runtime_identity(),
        }
        state["items"][checkpoint_id] = checkpoint
        state["current_id"] = checkpoint_id
        task_state.checkpoint_id = checkpoint_id
        self.session["runtime_identity"] = checkpoint["runtime_identity"]
        self.session_path = self.session_store.save(self.session)
        return checkpoint

    def infer_next_step(self, task_state):
        if task_state.status == "completed":
            return "No next step recorded."
        if task_state.stop_reason == "step_limit_reached":
            return "Resume from the latest checkpoint and continue the task."
        if task_state.last_tool:
            return f"Decide the next action after {task_state.last_tool}."
        return "Continue the task from the latest checkpoint."

    def update_memory_after_tool(self, name, args, result):
        """把少量高价值工具结果沉淀到 working memory。

        为什么存在：
        并不是每个工具结果都值得长期带进下一轮 prompt。完整结果已经进了
        `history`，这里只挑少量“下一轮大概率还会用到”的事实做提纯，
        例如最近读写过哪些文件、某个文件读出来的短摘要。

        输入 / 输出：
        - 输入：工具名 `name`、参数 `args`、执行结果 `result`
        - 输出：无显式返回值，副作用是更新 `self.memory`

        在 agent 链路里的位置：
        它发生在 `run_tool()` 真正执行完工具之后、下一轮 prompt 组装之前。
        也就是说：工具结果先进入完整历史，再由这个函数择优沉淀成轻量记忆。
        """
        if not self.feature_enabled("memory"):
            return
        path = args.get("path")
        if not path:
            return

        canonical_path = self.memory.canonical_path(path)
        # 不是所有工具结果都进入工作记忆。
        # 读文件会生成摘要；写文件/patch 会让旧摘要失效，因为它们可能过期了。
        if name in {"read_file", "write_file", "patch_file"}:
            self.memory.remember_file(canonical_path)
        if name == "read_file":
            summary = memorylib.summarize_read_result(result)
            self.memory.set_file_summary(canonical_path, summary)
            self.memory.append_note(
                summary, tags=(canonical_path,), source=canonical_path
            )
        elif name in {"write_file", "patch_file"}:
            self.memory.invalidate_file_summary(canonical_path)

    def note_tool(self, name, args, result):
        self.update_memory_after_tool(name, args, result)

    def record_process_note_for_tool(self, name, metadata):
        status = str(metadata.get("tool_status", "")).strip()
        if status not in {"partial_success", "error", "rejected"}:
            return
        affected_paths = [
            str(path).strip()
            for path in metadata.get("affected_paths", [])
            if str(path).strip()
        ]
        path_text = ", ".join(affected_paths) or "workspace"
        if status == "partial_success":
            text = f"{name} partial_success on {path_text}; inspect diff before retry"
        elif status == "error":
            text = f"{name} error on {path_text}; check the failure before retry"
        else:
            text = f"{name} rejected; choose a different action before retry"
        tags = ["process", status, *affected_paths]
        self.memory.append_note(text, tags=tuple(tags), source=name, kind="process")
        self.session["memory"] = self.memory.to_dict()

    def reject_durable_reason(self, note_text):
        text = str(note_text or "").strip()
        lowered = text.lower()
        if not text:
            return "empty"
        if REDACTED_VALUE in text or SECRET_SHAPED_TEXT_PATTERN.search(text):
            return "secret_shaped"
        checkpoint_like_prefixes = (
            "current goal",
            "current blocker",
            "next step",
            "current phase",
            "key files",
            "freshness",
            "当前目标",
            "当前卡点",
            "下一步",
            "当前阶段",
            "关键文件",
            "已完成",
            "已排除",
        )
        if any(lowered.startswith(prefix) for prefix in checkpoint_like_prefixes):
            return "transient_task_state"
        if (
            re.search(r"(?i)\b(stdout|stderr|traceback|exit_code)\b", text)
            or len(text) > 220
        ):
            return "noisy_output"
        return ""

    def extract_durable_promotions(self, user_message, final_answer):
        user_text = str(user_message or "")
        if not (
            DURABLE_MEMORY_INTENT_PATTERN.search(user_text)
            or DURABLE_MEMORY_INTENT_ZH_PATTERN.search(user_text)
        ):
            return [], []
        promotions = []
        rejections = []
        for line in str(final_answer or "").splitlines():
            text = line.strip()
            if not text or REDACTED_VALUE in text:
                continue
            for topic, pattern in DURABLE_MEMORY_LINE_PATTERNS:
                match = pattern.match(text)
                if not match:
                    continue
                note_text = match.group(1).strip()
                if note_text:
                    reason = self.reject_durable_reason(note_text)
                    if reason:
                        rejections.append(f"{topic}:{reason}")
                        break
                    promotions.append((topic, note_text))
                break
        return promotions, rejections

    def promote_durable_memory(self, user_message, final_answer):
        promotions, rejections = self.extract_durable_promotions(
            user_message, final_answer
        )
        promoted, superseded = self.memory.promote_durable(promotions)
        self.session["memory"] = self.memory.to_dict()
        self.last_durable_promotions = promoted
        self.last_durable_rejections = rejections
        self.last_durable_superseded = superseded
        return promoted, rejections, superseded

    def ask(self, user_message, run_id=""):
        """执行一次完整的 agent 回合，直到产出最终答案或命中停止条件。

        为什么存在：
        `ask()` 是整个 runtime 的总调度器。它把“用户提一个请求”扩展成一条
        可持续推进的控制循环：记录会话、组 prompt、调用模型、执行工具、
        写 trace/report、更新状态，直到模型给出最终答案或系统主动停下。

        输入 / 输出：
        - 输入：`user_message`，即用户这一次的任务描述
        - 输出：字符串形式的最终回答；如果中途达到步数上限或重试上限，
          返回的是一条停止原因说明

        在 agent 链路里的位置：
        它是 CLI 和底层工具/模型之间的核心桥梁。CLI 收到用户输入后基本只做
        一件事：调用 `agent.ask()`。而 `ask()` 内部再去驱动 `ContextManager`
        组 prompt、`model_client.complete()` 调模型、`run_tool()` 执行动作。
        如果新人想理解 pico 是怎么“从一句话跑成一个 agent 流程”的，
        这里就是最关键的入口。
        """
        run_started_at = time.monotonic()
        run_started_wall = now()
        self.memory.set_task_summary(user_message)
        self.record({"role": "user", "content": user_message, "created_at": now()})

        task_run_id = (
            self.validate_external_run_id(run_id) if run_id else self.new_run_id()
        )
        task_state = TaskState.create(
            run_id=task_run_id, task_id=self.new_task_id(), user_request=user_message
        )
        task_state.resume_status = self.resume_state.get(
            "status", CHECKPOINT_NONE_STATUS
        )
        self.current_task_state = task_state
        self.current_run_usage = []
        self.current_run_source_reads = []
        self.current_planning = self.new_planning_state()
        if self.working_state is not None:
            # Phase 2: Task-local Working State 生命周期 = 本次 ask。
            self.working_state = WorkingState()
            self.working_state.set_goal(user_message)
        if self.context_compiler is not None:
            # Phase 2.6: 压缩计数 / summary 栈 / hysteresis 状态同样 task-local。
            self.context_compiler.reset_run_state()
        if self.memory_v2_enabled():
            # Phase 3: Memory 2.0 run-local 状态清零；task-start retrieval 在
            # run_started trace 之后执行（保持事件顺序契约）。
            self.memory_v2.reset_run_state()
            self.memory_v2.set_run_context(
                task_id=task_state.task_id, run_id=task_state.run_id
            )
        self.current_run_dir = self.run_store.start_run(task_state)
        self.emit_run_status(
            task_state,
            "building_context",
            "Building context",
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.emit_trace(
            task_state,
            "run_started",
            {
                "task_id": task_state.task_id,
                "user_request": clip(user_message, 300),
            },
        )
        # Phase 3: task-start retrieval（run_started 之后，保持 trace 顺序）。
        if self.memory_v2_enabled():
            self._refresh_memory_retrieval(user_message, force=True)

        tool_steps = 0
        attempts = 0
        research_steps = 0
        research_budget = task_policy.research_tool_budget(user_message)
        finalization_required = False
        finalization_rejections = 0
        # Phase 1: 每次 ask() 独立跟踪 stuck 状态，不跨 run 累积。
        self.watchdog = ProgressWatchdog(file_hash_fn=self._file_freshness)
        # Phase 2.6: edit-decision 进展跟踪同样 task-local。
        self.edit_decision_watchdog = EditDecisionWatchdog(
            file_hash_fn=self._file_freshness
        )
        step_budget = self.effective_step_budget
        attempt_cap = (
            max(step_budget * 3, step_budget + 4)
            if step_budget is not None
            else DEFAULT_INTERACTIVE_ATTEMPT_CAP
        )
        emergency_cap = (
            None
            if step_budget is not None
            else int(self.emergency_cap or DEFAULT_INTERACTIVE_EMERGENCY_CAP)
        )
        native_mode = bool(getattr(self.model_client, "supports_native_tools", False))
        native_messages = []
        pending_native_calls = []
        if native_mode:
            native_messages = [
                {
                    "role": "system",
                    "content": "You are CodeCub, a local coding agent. Use the supplied tools when workspace evidence or changes are required. Do not emit XML tool syntax.",
                }
            ]
            self.emit_trace(
                task_state,
                "model_protocol_selected",
                {
                    "model_protocol": "native_tools",
                    "provider_protocol": getattr(
                        getattr(self.model_client, "connection_profile", None),
                        "protocol",
                        "",
                    ),
                },
            )
        else:
            self.emit_trace(
                task_state,
                "model_protocol_selected",
                {
                    "model_protocol": "legacy_text",
                    "provider_protocol": getattr(
                        getattr(self.model_client, "connection_profile", None),
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
            if self.cancellation_requested(task_state):
                return self.stop_user_canceled_run(
                    task_state, run_started_wall, run_started_at
                )
            if step_budget is not None:
                if tool_steps >= step_budget or attempts >= attempt_cap:
                    return self.stop_limited_run(
                        task_state,
                        user_message,
                        attempts,
                        attempt_cap,
                        tool_steps,
                        run_started_wall,
                        run_started_at,
                    )
            else:
                if tool_steps >= emergency_cap:
                    return self.stop_emergency_cap_run(
                        task_state,
                        user_message,
                        emergency_cap,
                        run_started_wall,
                        run_started_at,
                    )
                if attempts >= attempt_cap:
                    return self.stop_limited_run(
                        task_state,
                        user_message,
                        attempts,
                        attempt_cap,
                        tool_steps,
                        run_started_wall,
                        run_started_at,
                    )
            attempts += 1
            task_state.record_attempt()
            self.run_store.write_task_state(task_state)
            self.emit_run_status(
                task_state,
                "building_context",
                "Building context",
                started_at=run_started_wall,
                run_started_at=run_started_at,
            )
            prompt_started_at = time.monotonic()

            def emit_context_status(phase, label, detail=""):
                self.emit_run_status(
                    task_state,
                    phase,
                    label,
                    detail=detail,
                    started_at=run_started_wall,
                    run_started_at=run_started_at,
                )
                self.emit_trace(
                    task_state,
                    "context_step_started",
                    {
                        "phase": phase,
                        "detail": clip(detail, 300),
                    },
                )

            prompt, prompt_metadata = self._build_prompt_and_metadata(
                user_message,
                status_callback=emit_context_status,
                task_state=task_state,
            )
            if native_mode:
                prompt_metadata["model_protocol"] = "native_tools"
                if len(native_messages) == 1:
                    native_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "For file tools, use workspace-relative paths; "
                                "shell commands already execute at the workspace root.\n\n"
                                + user_message
                            ),
                        }
                    )
            self.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                },
            )
            # 说明已有检查点的关键文件部分过期（内容变了）
            if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
                checkpoint = self.create_checkpoint(
                    task_state, user_message, trigger="freshness_mismatch"
                )
                self.run_store.write_task_state(task_state)
                self.emit_trace(
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
                self.emit_trace(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(
                            prompt_metadata.get("runtime_identity_mismatch_fields", [])
                        ),
                    },
                )
                checkpoint = self.create_checkpoint(
                    task_state, user_message, trigger="workspace_mismatch"
                )
                self.run_store.write_task_state(task_state)
                self.emit_trace(
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
            if self.context_compiler is not None:
                current_count = self.context_compiler.compression_count
                compiler_compression = current_count > getattr(
                    self, "_last_compiler_compression_count", 0
                )
                self._last_compiler_compression_count = current_count
            if prompt_metadata.get("budget_reductions") or compiler_compression:
                checkpoint = self.create_checkpoint(
                    task_state, user_message, trigger="context_reduction"
                )
                self.run_store.write_task_state(task_state)
                self.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "context_reduction",
                    },
                )
            self.emit_trace(
                task_state,
                "model_requested",
                {
                    "attempts": task_state.attempts,
                    "tool_steps": task_state.tool_steps,
                    "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
                },
            )
            if self.cancellation_requested(task_state):
                return self.stop_user_canceled_run(
                    task_state, run_started_wall, run_started_at
                )
            self.emit_run_status(
                task_state,
                "model_request",
                "Requesting model response",
                detail=str(getattr(self.model_client, "model", "")),
                started_at=run_started_wall,
                run_started_at=run_started_at,
            )
            prompt_cache_key = None
            prompt_cache_retention = None
            if self.feature_enabled("prompt_cache") and getattr(
                self.model_client, "supports_prompt_cache", False
            ):
                # 只有后端明确支持时，才把稳定前缀的 hash 作为 cache key 发出去。
                prompt_cache_key = prompt_metadata.get("prompt_cache_key")
                prompt_cache_retention = "in_memory"
            model_started_at = time.monotonic()
            self.emit_run_status(
                task_state,
                "model_streaming",
                "Receiving model response",
                detail=str(getattr(self.model_client, "model", "")),
                started_at=run_started_wall,
                run_started_at=run_started_at,
            )
            stream_filter = FinalAnswerDeltaFilter(
                lambda text: self.emit_app_event(
                    "assistant_delta", task_state, {"text": text}
                )
            )
            self.last_prompt_metadata = prompt_metadata
            try:
                model_kwargs = {
                    "prompt_cache_key": prompt_cache_key,
                    "prompt_cache_retention": prompt_cache_retention,
                }
                if getattr(
                    self.model_client, "supports_structured_prompt_cache", False
                ):
                    model_kwargs["stable_prefix"] = self.prefix
                if native_mode:
                    connection_profile = getattr(
                        self.model_client, "connection_profile", None
                    )
                    supports_tool_choice = bool(
                        getattr(connection_profile, "supports_tool_choice", False)
                    )
                    edit_decision = (
                        self.requires_workspace_change
                        and self.current_planning.get("action_readiness")
                        == "action_expected"
                        and not self.current_planning.get("workspace_change_count")
                    )
                    native_tools = toolkit.native_tool_definitions(self.tools)
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
                                            "required": ["decision", "tool", "arguments"],
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
                        self.emit_trace(
                            task_state,
                            "phase_transition",
                            {
                                "phase": "edit_decision",
                                "edit_decision_index": self.current_planning.get(
                                    "edit_decision_count", 0
                                )
                                + 1,
                                "compatibility_mode": not supports_tool_choice,
                            },
                        )
                    if pending_native_calls:
                        raw = pending_native_calls.pop(0)
                    else:
                        # Phase 2: native 消息经 Context Compiler 编译（不压缩时
                        # 原样返回，超预算时保持 assistant/tool 原子组压缩）。
                        if self.context_compiler is not None:
                            self.working_state.refresh_fact_freshness(self.root)
                            memory_layer, memory_meta = self._memory_layer()
                            compiled_native, compiler_meta = self.context_compiler.compile_native(
                                user_message,
                                working_state=self.working_state,
                                native_messages=native_messages,
                                pinned_extra=self._pinned_extra(user_message),
                                memory_layer=memory_layer,
                                memory_meta=memory_meta,
                            )
                            if compiler_meta.get("should_compress"):
                                self.emit_trace(
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
                                self.emit_trace(
                                    task_state,
                                    "compression_started",
                                    {"mode": "native"},
                                )
                                self.emit_trace(
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
                        raw = self.model_client.complete_with_tools(
                            native_messages,
                            native_tools,
                            self.max_new_tokens,
                            tool_choice=tool_choice,
                        )
                elif hasattr(self.model_client, "stream_complete"):
                    raw = self.model_client.stream_complete(
                        prompt,
                        self.max_new_tokens,
                        on_delta=stream_filter.feed,
                        **model_kwargs,
                    )
                else:
                    raw = self.model_client.complete(
                        prompt,
                        self.max_new_tokens,
                        **model_kwargs,
                    )
            except Exception as exc:
                return self.stop_model_error_run(
                    task_state, exc, model_started_at, run_started_wall, run_started_at
                )
            completion_metadata = dict(
                getattr(self.model_client, "last_completion_metadata", {}) or {}
            )
            if self.cancellation_requested(task_state):
                return self.stop_user_canceled_run(
                    task_state, run_started_wall, run_started_at
                )
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
                        "session_id": self.session.get("id", ""),
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
                usage_record = self.redact_artifact(usage_record)
                self.run_store.append_usage(task_state, usage_record)
                self.current_run_usage.append(usage_record)
                try:
                    stored = self.usage_store.record(usage_record)
                    run_snapshot = build_usage_snapshot(
                        self.current_run_usage,
                        "run",
                        session_id=self.session.get("id", ""),
                        run_id=task_state.run_id,
                    )
                    session_snapshot = (
                        (stored or {}).get("snapshot")
                        if isinstance(stored, dict)
                        else None
                    )
                    if isinstance(session_snapshot, dict):
                        self.emit_app_event(
                            "usage_updated",
                            task_state,
                            {
                                "schema_version": 2,
                                "usage_id": usage_record["usage_id"],
                                "run_snapshot": run_snapshot,
                                "session_snapshot": session_snapshot,
                            },
                        )
                except Exception as exc:
                    self.emit_trace(
                        task_state,
                        "usage_persistence_warning",
                        {
                            "error_type": exc.__class__.__name__,
                            "message": self.redact_text(str(exc)),
                        },
                    )
                self.emit_trace(
                    task_state,
                    "model_usage_recorded",
                    {
                        "usage_id": usage_record["usage_id"],
                        "connection_profile_id": usage_record.get(
                            "connection_profile_id", ""
                        ),
                    },
                )
            self.last_completion_metadata = completion_metadata
            self.last_prompt_metadata = prompt_metadata
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
                        and self.requires_workspace_change
                        and self.current_planning.get("action_readiness")
                        == "action_expected"
                        and not self.current_planning.get("workspace_change_count")
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
                            self.emit_trace(
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
                        self.current_planning["edit_decision_count"] = (
                            self.current_planning.get("edit_decision_count", 0) + 1
                        )
                        self.edit_decision_watchdog.record_decision(decision)
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
                            self.current_planning["invalid_edit_decision_count"] = (
                                self.current_planning.get(
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
                                self.edit_decision_watchdog.classify_evidence_request(
                                    requested_name, requested_args, task_state.tool_steps
                                )
                            )
                            if classification.progress:
                                self.current_planning["evidence_request_count"] = (
                                    self.current_planning.get(
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
                                    },
                                )
                            else:
                                # 重复 evidence 且无 workspace change / 文件 hash 未变：
                                # 拒绝执行（不烧真实工具步），并把 no-progress 事件喂给主
                                # Watchdog，使“重复 evidence”也能 suspected -> recovery
                                # -> stuck_confirmed。
                                self.edit_decision_watchdog.record_no_progress(
                                    classification
                                )
                                self.emit_trace(
                                    task_state,
                                    "edit_decision_no_progress",
                                    {
                                        "tool": requested_name,
                                        "reason": classification.reason,
                                        "edit_decision_count": self.current_planning.get(
                                            "edit_decision_count", 0
                                        ),
                                        "no_progress_streak": self.edit_decision_watchdog.no_progress_streak,
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
                                watchdog_decision = self._advance_watchdog(
                                    task_state,
                                    requested_name,
                                    requested_args,
                                    "rejected: repeated evidence request",
                                    # 拒绝的事件没有真实工具执行，tool_steps 不前进；
                                    # 用单调递增的 attempts 作为 watchdog step，
                                    # 保证 recovery 窗口能正常计时。
                                    task_state.attempts,
                                    metadata=rejected_meta,
                                )
                                if watchdog_decision.suspected_now:
                                    # 重复 evidence 触发 stuck suspected：注入 Recovery
                                    # Turn（与工具执行路径一致），继续运行。
                                    self.record(
                                        {
                                            "role": "assistant",
                                            "content": RECOVERY_TURN_PROMPT,
                                            "created_at": now(),
                                        }
                                    )
                                    native_messages.append(
                                        {"role": "user", "content": RECOVERY_TURN_PROMPT}
                                    )
                                    self.emit_run_status(
                                        task_state,
                                        "stuck_suspected",
                                        "Recovery turn",
                                        detail=watchdog_decision.stuck_pattern,
                                        started_at=run_started_wall,
                                        run_started_at=run_started_at,
                                    )
                                    self.run_store.write_task_state(task_state)
                                    continue
                                if watchdog_decision.confirmed_now:
                                    return self.stop_stuck_confirmed_run(
                                        task_state,
                                        user_message,
                                        run_started_wall,
                                        run_started_at,
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
                                },
                            )
                    else:
                        kind, payload = (
                            "tool",
                            {
                                "name": call.name,
                                "args": call.arguments,
                                "tool_call_id": call.id,
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
                                raw_metadata={"queued_native_call": True},
                            )
                            for item in raw.tool_calls[1:]
                        )
                    if len(raw.tool_calls) > 1:
                        self.emit_trace(
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
                            Pico.retry_notice("model returned an empty final response"),
                        )
                        # 让 native 模型看到拒绝原因（空 final 没有未应答的
                        # assistant tool call，直接补一条 user 消息）。
                        native_messages.append(
                            {"role": "user", "content": str(payload)}
                        )
                    else:
                        kind, payload = "final", final_text
            else:
                kind, payload = self.parse(raw)
            self.emit_trace(
                task_state,
                "model_parsed",
                {
                    "kind": kind,
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
                        self.current_run_source_reads, research_steps, research_budget
                    )
                    self.record(
                        {"role": "assistant", "content": notice, "created_at": now()}
                    )
                    self.emit_trace(
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
                        return self.stop_finalization_failed_run(
                            task_state, user_message, run_started_wall, run_started_at
                        )
                    self.run_store.write_task_state(task_state)
                    continue
                tool_steps += 1
                task_state.record_tool(name)
                tool_started_at = time.monotonic()
                self.emit_run_status(
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
                    read_notice, read_classification = self.read_guard_notice(args)
                result = self.run_tool(name, args)
                if read_notice:
                    result = f"{read_notice}\n\n{result}"
                if name == "read_file":
                    self._last_tool_result_metadata["read_evidence_classification"] = (
                        read_classification
                    )
                    if read_classification == "avoidable_repeated_read":
                        self.current_planning["avoidable_repeated_read_calls"] += 1
                    elif read_classification == "evidence_evicted_reread":
                        self.current_planning["evidence_evicted_reread_calls"] += 1
                    self.record_read_evidence(args, result, task_state.tool_steps)
                elif bool(
                    (self._last_tool_result_metadata or {}).get("workspace_changed")
                ):
                    self.invalidate_evidence_for_paths(
                        (self._last_tool_result_metadata or {}).get("affected_paths")
                    )
                if self.cancellation_requested(task_state):
                    return self.stop_user_canceled_run(
                        task_state, run_started_wall, run_started_at
                    )
                self.record(
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
                    self.current_run_source_reads.append(str(args.get("path")))
                if research_budget is not None and task_policy.is_research_tool(name):
                    research_steps += 1
                self.run_store.write_task_state(task_state)
                tool_event_payload = {
                    "name": name,
                    "args": args,
                    "result": clip(result, 500),
                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                    **dict(self._last_tool_result_metadata or {}),
                }
                self.emit_trace(task_state, "tool_executed", tool_event_payload)
                if self.event_handler is not None:
                    self.event_handler(
                        "tool_executed", dict(tool_event_payload), self, task_state
                    )
                semantic_repeat = self.update_planning_state(
                    name, args, self._last_tool_result_metadata, task_state.tool_steps
                )
                if self.working_state is not None:
                    self.working_state.update_from_tool_event(
                        name,
                        args,
                        self._last_tool_result_metadata,
                        result,
                        task_state.tool_steps,
                        self.root,
                    )
                    self.emit_trace(
                        task_state,
                        "working_state_updated",
                        {
                            "step": task_state.tool_steps,
                            "changed_files": list(self.working_state.changed_files),
                            "verification_status": (
                                self.working_state.verification[-1].get("status", "")
                                if self.working_state.verification
                                else ""
                            ),
                        },
                    )
                # Phase 3: blocker / relevant-symbol / changed-file 实质变化时
                # 重新 retrieval（避免每 tool step 全库检索）。
                if self.memory_v2_enabled() and (
                    self._memory_signature() != self._memory_retrieval_signature
                ):
                    self._refresh_memory_retrieval(user_message)
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
                        self.edit_decision_watchdog.mark_evidence_executed(
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
                    self.emit_trace(
                        task_state,
                        "edit_decision_feedback",
                        {"decision": decision, "tool": name},
                    )
                if semantic_repeat:
                    self.emit_trace(
                        task_state,
                        "semantic_redundant_exploration",
                        {"tool_name": name, "tool_step": task_state.tool_steps},
                    )
                # 纯 observability：exploration / implementation warning 仍会发出，
                # 但它们不再直接导致停机。是否卡住只由 Progress Watchdog 判定。
                self.maybe_emit_exploration_warning(task_state)
                self.maybe_emit_implementation_warning(task_state)
                checkpoint = self.create_checkpoint(
                    task_state, user_message, trigger="tool_executed"
                )
                self.run_store.write_task_state(task_state)
                self.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "tool_executed",
                    },
                )
                # Phase 1: Progress Watchdog 是唯一的 stuck 决策来源。
                watchdog_decision = self._advance_watchdog(
                    task_state, name, args, result, task_state.tool_steps
                )
                if watchdog_decision.suspected_now:
                    # 第一次疑似卡住：注入 Recovery Turn 提示（不含任务答案），
                    # 继续运行，不直接结束。
                    self.record(
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
                    if self.memory_v2_enabled():
                        self._refresh_memory_retrieval(user_message)
                    self.emit_run_status(
                        task_state,
                        "stuck_suspected",
                        "Recovery turn",
                        detail=watchdog_decision.stuck_pattern,
                        started_at=run_started_wall,
                        run_started_at=run_started_at,
                    )
                    self.run_store.write_task_state(task_state)
                    continue
                if watchdog_decision.confirmed_now:
                    return self.stop_stuck_confirmed_run(
                        task_state,
                        user_message,
                        run_started_wall,
                        run_started_at,
                    )
                if research_budget is not None and research_steps >= research_budget:
                    finalization_required = True
                    notice = task_policy.finalization_notice(
                        self.current_run_source_reads, research_steps, research_budget
                    )
                    self.record(
                        {"role": "assistant", "content": notice, "created_at": now()}
                    )
                    self.emit_trace(
                        task_state,
                        "finalization_required",
                        {
                            "source_reads": list(self.current_run_source_reads),
                            "research_steps": research_steps,
                            "research_budget": research_budget,
                        },
                    )
                    self.emit_run_status(
                        task_state,
                        "finalization_required",
                        "Generating answer from collected evidence",
                        detail=f"{research_steps}/{research_budget}",
                        started_at=run_started_wall,
                        run_started_at=run_started_at,
                    )
                continue

            if kind == "retry":
                self.record(
                    {"role": "assistant", "content": payload, "created_at": now()}
                )
                self.run_store.write_task_state(task_state)
                continue

            if (
                task_policy.requires_source_evidence(user_message)
                and not self.current_run_source_reads
            ):
                notice = task_policy.evidence_retry_notice()
                self.record(
                    {"role": "assistant", "content": notice, "created_at": now()}
                )
                self.emit_trace(
                    task_state,
                    "evidence_insufficient",
                    {"required": "source_file_read", "source_reads": []},
                )
                self.run_store.write_task_state(task_state)
                continue
            if (
                native_mode
                and self.requires_workspace_change
                and self.current_planning["workspace_change_count"]
                > self.current_planning["last_verified_change_count"]
            ):
                notice = (
                    "A workspace change was made but has not been verified. "
                    "Run one focused verification command now, then provide the final answer."
                )
                self.record(
                    {"role": "assistant", "content": notice, "created_at": now()}
                )
                if native_mode:
                    native_messages.append({"role": "user", "content": notice})
                self.emit_trace(
                    task_state,
                    "verification_required_after_change",
                    {
                        "workspace_change_count": self.current_planning[
                            "workspace_change_count"
                        ],
                        "last_verified_change_count": self.current_planning[
                            "last_verified_change_count"
                        ],
                    },
                )
                self.run_store.write_task_state(task_state)
                continue
            # Native providers may return a normal finish with an empty text
            # field.  `raw` is a ModelResponse in that mode, never fallback
            # content for a final answer.
            final = str(payload or "").strip()
            return self.finish_successful_run(
                task_state, user_message, final, run_started_wall, run_started_at
            )

    def stop_finalization_failed_run(
        self, task_state, user_message, run_started_wall, run_started_at
    ):
        final = "Stopped because the model did not produce a final answer after the research budget was exhausted."
        task_state.stop("finalization_failed", final_answer=final)
        self.record({"role": "assistant", "content": final, "created_at": now()})
        self.promote_durable_memory(user_message, final)
        self.extract_memory_v2(user_message, final)
        self.run_store.write_task_state(task_state)
        self.emit_run_finished(task_state, final, run_started_at)
        self.emit_run_status(
            task_state,
            "failed",
            "Failed",
            detail="finalization_failed",
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.write_final_report(task_state)
        return final

    def write_final_report(self, task_state):
        # Phase 3: run 结束时的 stale-revalidation 记账（在 report 生成前）。
        if self.memory_v2_enabled():
            try:
                self.memory_v2.finalize_run()
            except Exception:
                pass
        self.run_store.write_report(
            task_state, self.redact_artifact(self.build_report(task_state))
        )

    def emit_run_finished(self, task_state, final, run_started_at):
        self.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )

    def emit_checkpoint_created(self, task_state, checkpoint, trigger):
        self.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": trigger,
            },
        )

    def finish_successful_run(
        self, task_state, user_message, final, run_started_wall, run_started_at
    ):
        self.emit_run_status(
            task_state,
            "finalizing",
            "Finalizing response",
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.record({"role": "assistant", "content": final, "created_at": now()})
        task_state.finish_success(final)
        self.promote_durable_memory(user_message, final)
        self.extract_memory_v2(user_message, final)
        checkpoint = self.create_checkpoint(
            task_state, user_message, trigger="run_finished"
        )
        self.run_store.write_task_state(task_state)
        self.emit_checkpoint_created(task_state, checkpoint, "run_finished")
        self.emit_run_finished(task_state, final, run_started_at)
        self.emit_run_status(
            task_state,
            "completed",
            "Completed",
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.write_final_report(task_state)
        return final

    def stop_limited_run(
        self,
        task_state,
        user_message,
        attempts,
        attempt_cap,
        tool_steps,
        run_started_wall,
        run_started_at,
    ):
        budget = self.effective_step_budget
        if attempts >= attempt_cap and (budget is None or tool_steps < budget):
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
            task_state.stop_retry_limit(final)
        else:
            final = "Stopped after reaching the step limit without a final answer."
            task_state.stop_step_limit(final)
        self.record({"role": "assistant", "content": final, "created_at": now()})
        self.promote_durable_memory(user_message, final)
        self.extract_memory_v2(user_message, final)
        self.run_store.write_task_state(task_state)
        trigger = task_state.stop_reason or "run_stopped"
        checkpoint = self.create_checkpoint(task_state, user_message, trigger=trigger)
        self.emit_checkpoint_created(task_state, checkpoint, trigger)
        self.emit_run_finished(task_state, final, run_started_at)
        self.emit_run_status(
            task_state,
            "failed",
            "Failed",
            detail=task_state.stop_reason,
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.write_final_report(task_state)
        return final

    def _advance_watchdog(self, task_state, name, args, result, step, metadata=None):
        """把一次工具事件交给 Progress Watchdog，并发射 trace 事件。

        suspected_now 触发时由 ask() 负责注入 Recovery Turn 提示；本方法只做
        watchdog 推进与可观测性记录。返回 WatchdogDecision。

        `metadata` 缺省使用 `_last_tool_result_metadata`（真实工具执行路径）；
        也可显式传入（例如 EditDecisionWatchdog 拒绝重复 evidence 时合成的
        rejected 事件，此时没有真实工具执行）。
        """
        decision = self.watchdog.record_tool_event(
            name,
            args,
            self._last_tool_result_metadata if metadata is None else metadata,
            result,
            step,
        )
        for signal in decision.progress_signals:
            self.emit_trace(
                task_state,
                "progress_detected",
                {
                    "kind": signal.kind,
                    "reason": self.redact_text(signal.reason),
                    "step": signal.step,
                },
            )
        if decision.suspected_now:
            self.emit_trace(
                task_state,
                "stuck_suspected",
                {
                    "pattern": decision.stuck_pattern,
                    "step": step,
                    "no_progress_score": self.watchdog.no_progress_score,
                },
            )
            self.watchdog.begin_recovery(step)
            self.emit_trace(
                task_state,
                "recovery_turn_started",
                {
                    "step": step,
                    "recovery_turn_count": self.watchdog.recovery_turn_count,
                },
            )
        elif decision.recovered_now:
            self.emit_trace(
                task_state,
                "recovery_turn_finished",
                {
                    "success": True,
                    "step": step,
                    "recovery_success_count": self.watchdog.recovery_success_count,
                },
            )
        elif decision.confirmed_now:
            self.emit_trace(
                task_state,
                "stuck_confirmed",
                {
                    "pattern": decision.stuck_pattern,
                    "step": step,
                    "stuck_confirmed_count": self.watchdog.stuck_confirmed_count,
                },
            )
        return decision

    def stop_stuck_confirmed_run(
        self,
        task_state,
        user_message,
        run_started_wall,
        run_started_at,
    ):
        """STUCK_CONFIRMED：experiment 以 stop_reason 结束；interactive graceful stop。"""
        pattern = self.watchdog.current_pattern or "no_progress_window"
        if self.runtime_mode == RUNTIME_MODE_INTERACTIVE:
            last_reason = self.watchdog.last_progress_reason or "the start of the task"
            last_step = self.watchdog.last_progress_step or 0
            final = (
                "Agent paused because it appears stuck.\n"
                f"Current blocker: repeated recovery turns did not produce new "
                f"evidence, workspace changes, or verification information.\n"
                f"Last useful progress: {last_reason} (step {last_step})."
            )
        else:
            final = (
                "Stopped because the agent appeared stuck and did not recover "
                f"(pattern: {pattern})."
            )
        task_state.stop(STOP_REASON_STUCK_CONFIRMED, final_answer=final)
        self.record({"role": "assistant", "content": final, "created_at": now()})
        self.promote_durable_memory(user_message, final)
        self.extract_memory_v2(user_message, final)
        self.run_store.write_task_state(task_state)
        checkpoint = self.create_checkpoint(
            task_state, user_message, trigger=STOP_REASON_STUCK_CONFIRMED
        )
        self.emit_checkpoint_created(
            task_state, checkpoint, STOP_REASON_STUCK_CONFIRMED
        )
        self.emit_run_finished(task_state, final, run_started_at)
        self.emit_run_status(
            task_state,
            "failed",
            "Stopped",
            detail=task_state.stop_reason,
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.write_final_report(task_state)
        return final

    def stop_emergency_cap_run(
        self,
        task_state,
        user_message,
        emergency_cap,
        run_started_wall,
        run_started_at,
    ):
        """Interactive 模式的 emergency fuse：只兜 Runtime Bug / watchdog 漏检 / runaway。"""
        final = (
            "Stopped after reaching the emergency step cap "
            f"({emergency_cap}) without a final answer."
        )
        task_state.stop(STOP_REASON_EMERGENCY_CAP_REACHED, final_answer=final)
        self.record({"role": "assistant", "content": final, "created_at": now()})
        self.promote_durable_memory(user_message, final)
        self.extract_memory_v2(user_message, final)
        self.run_store.write_task_state(task_state)
        self.emit_trace(
            task_state,
            "emergency_cap_reached",
            {
                "cap": int(emergency_cap or 0),
                "tool_steps": task_state.tool_steps,
            },
        )
        checkpoint = self.create_checkpoint(
            task_state, user_message, trigger=STOP_REASON_EMERGENCY_CAP_REACHED
        )
        self.emit_checkpoint_created(
            task_state, checkpoint, STOP_REASON_EMERGENCY_CAP_REACHED
        )
        self.emit_run_finished(task_state, final, run_started_at)
        self.emit_run_status(
            task_state,
            "failed",
            "Stopped",
            detail=task_state.stop_reason,
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.write_final_report(task_state)
        return final

    def run_tool(self, name, args):
        """执行一次工具调用，并在执行前后套上完整护栏。

        为什么存在：
        在 agent 系统里，真正危险的不是“模型会不会想调用工具”，而是
        “平台有没有在执行前把边界守住”。这个函数就是工具层的总闸口：
        所有工具调用都必须先经过它，不能让模型直接碰到底层函数。

        输入 / 输出：
        - 输入：工具名 `name`，参数字典 `args`
        - 输出：字符串结果。无论是成功结果还是错误信息，都会统一返回文本，
          这样模型下一轮都能继续消费这份反馈。

        在 agent 链路里的位置：
        它位于 `ask()` 的“模型决定要调用工具”之后，是控制循环里真正把模型
        意图落到外部世界的一步。因此这里串起了几乎所有安全与可控设计：
        工具是否存在、参数是否合法、是否重复、是否需要审批、执行结果是否裁剪、
        是否需要回写记忆。
        """
        # 工具执行不是“直接调函数”，而是一条带护栏的流水线：
        # 工具是否存在 -> 参数是否合法 -> 是否重复调用 -> 是否通过审批
        # -> 真正执行 -> 更新记忆。
        tool = self.tools.get(name)
        if tool is None:
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "unknown_tool",
                "security_event_type": "",
                "risk_level": "high",
                "read_only": False,
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return f"error: unknown tool '{name}'"
        try:
            self.validate_tool(name, args)
        except Exception as exc:
            example = self.tool_example(name)
            message = f"error: invalid arguments for {name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            security_event_type = (
                "path_escape" if "path escapes workspace" in str(exc) else ""
            )
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "invalid_arguments",
                "security_event_type": security_event_type,
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return message
        if self.repeated_tool_call(name, args):
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "repeated_identical_call",
                "security_event_type": "",
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return (
                f"error: repeated identical tool call for {name}; "
                f"same no-progress action reached {REPEATED_NO_PROGRESS_LIMIT} consecutive attempts"
            )
        if tool["risky"] and not self.approve(name, args):
            self._last_tool_result_metadata = {
                "tool_status": "rejected",
                "tool_error_code": "approval_denied",
                "security_event_type": "read_only_block"
                if self.read_only
                else "approval_denied",
                "risk_level": "high",
                "read_only": False,
                "affected_paths": [],
                "workspace_changed": False,
                "diff_summary": [],
            }
            return f"error: approval denied for {name}"
        before_snapshot = self.capture_workspace_snapshot() if tool["risky"] else {}
        after_snapshot = before_snapshot
        try:
            result = clip(tool["run"](args))
            after_snapshot = (
                self.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            )
            affected_paths, diff_summary = self.diff_workspace_snapshots(
                before_snapshot, after_snapshot
            )
            workspace_changed = bool(affected_paths)
            tool_status = "ok"
            tool_error_code = ""
            if name == "run_shell":
                match = re.search(r"exit_code:\s*(-?\d+)", result)
                exit_code = int(match.group(1)) if match else 0
                if exit_code != 0 and workspace_changed:
                    tool_status = "partial_success"
                    tool_error_code = "tool_partial_success"
                elif exit_code != 0:
                    tool_status = "error"
                    tool_error_code = "tool_failed"
            self.update_memory_after_tool(name, args, result)
            self._last_tool_result_metadata = {
                "tool_status": tool_status,
                "tool_error_code": tool_error_code,
                "security_event_type": "",
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": affected_paths,
                "workspace_changed": workspace_changed,
                "workspace_fingerprint": self.workspace.fingerprint(),
                "diff_summary": diff_summary,
            }
            if workspace_changed:
                self._last_tool_result_metadata["code_index_refresh"] = (
                    self.code_index.refresh(affected_paths)
                )
            # Phase 3: 客观工具事件 → Evidence Store。
            self.record_memory_v2_evidence(name, args, result)
            self.record_process_note_for_tool(name, self._last_tool_result_metadata)
            return result
        except Exception as exc:
            after_snapshot = (
                self.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            )
            affected_paths, diff_summary = self.diff_workspace_snapshots(
                before_snapshot, after_snapshot
            )
            workspace_changed = bool(affected_paths)
            security_event_type = (
                "path_escape" if "path escapes workspace" in str(exc) else ""
            )
            self._last_tool_result_metadata = {
                "tool_status": "partial_success" if workspace_changed else "error",
                "tool_error_code": "tool_partial_success"
                if workspace_changed
                else "tool_failed",
                "security_event_type": security_event_type,
                "risk_level": "high" if tool["risky"] else "low",
                "read_only": not tool["risky"],
                "affected_paths": affected_paths,
                "workspace_changed": workspace_changed,
                "workspace_fingerprint": self.workspace.fingerprint(),
                "diff_summary": diff_summary,
            }
            self.record_process_note_for_tool(name, self._last_tool_result_metadata)
            return f"error: tool {name} failed: {exc}"

    def repeated_tool_call(self, name, args):
        # agent 很常见的一种坏循环，是在没有新信息的情况下反复发起同一调用。
        # 这里提前挡掉最简单的这种循环。
        tool_events = self.current_request_tool_events()
        required_previous_matches = REPEATED_NO_PROGRESS_LIMIT - 1
        if len(tool_events) < required_previous_matches:
            return False
        recent = tool_events[-required_previous_matches:]
        return all(item["name"] == name and item["args"] == args for item in recent)

    def current_request_tool_events(self):
        tool_events = []
        for item in reversed(self.session["history"]):
            if item.get("role") == "user":
                break
            if item.get("role") == "tool":
                tool_events.append(item)
        return list(reversed(tool_events))

    @staticmethod
    def new_planning_state():
        return {
            "consecutive_exploration": 0,
            "redundant_exploration_steps": 0,
            "productive_exploration_steps": 0,
            "rejected_steps": 0,
            "first_action_step": None,
            "exploration_steps_before_first_action": 0,
            "exploration_warning_count": 0,
            "warning_sent": False,
            "seen_reads": {},
            "seen_searches": set(),
            "seen_verifications": set(),
            "workspace_change_count": 0,
            "last_verified_change_count": 0,
            "first_workspace_change_step": None,
            "first_execution_step": None,
            "first_verification_after_change_step": None,
            "verification_steps": 0,
            "verification_before_first_action": 0,
            "productive_verification_steps": 0,
            "redundant_verification_steps": 0,
            "implementation_warning_count": 0,
            "implementation_warning_sent": False,
            "evidence_ledger": [],
            "evidence_eviction_count": 0,
            "avoidable_repeated_read_calls": 0,
            "evidence_evicted_reread_calls": 0,
            "read_guard_notices": set(),
            "action_readiness": "unknown",
            "action_readiness_transitions": [{"state": "unknown", "tool_step": 0}],
            "edit_decision_count": 0,
            "invalid_edit_decision_count": 0,
            "evidence_request_count": 0,
        }

    @staticmethod
    def set_action_readiness(state, readiness, tool_step):
        if state["action_readiness"] == readiness:
            return False
        state["action_readiness"] = readiness
        state["action_readiness_transitions"].append(
            {"state": readiness, "tool_step": tool_step}
        )
        return True

    def update_planning_state(self, name, args, metadata, tool_step):
        state = self.current_planning
        status = str((metadata or {}).get("tool_status", ""))
        if name in task_policy.ACTION_TOOLS:
            if state["first_action_step"] is None:
                state["first_action_step"] = tool_step
                state["exploration_steps_before_first_action"] = state[
                    "consecutive_exploration"
                ]
            state["consecutive_exploration"] = 0
            state["seen_reads"].clear()
            state["seen_searches"].clear()
            if bool((metadata or {}).get("workspace_changed")):
                state["workspace_change_count"] += 1
                if state["first_workspace_change_step"] is None:
                    state["first_workspace_change_step"] = tool_step
                self.set_action_readiness(state, "action_taken", tool_step)
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
            if redundant:
                state["redundant_verification_steps"] += 1
            else:
                state["productive_verification_steps"] += 1
            if epoch > state["last_verified_change_count"]:
                if state["first_verification_after_change_step"] is None:
                    state["first_verification_after_change_step"] = tool_step
                state["last_verified_change_count"] = epoch
            return redundant
        if name not in task_policy.EXPLORATION_TOOLS:
            return False
        state["consecutive_exploration"] += 1
        if (
            status == "ok"
            and self.requires_workspace_change
            and not state["workspace_change_count"]
        ):
            if name == "read_file" and task_policy.is_source_path(args.get("path")):
                profile = getattr(self.model_client, "connection_profile", None)
                requires_two_source_reads = bool(
                    getattr(self.model_client, "supports_native_tools", False)
                    and profile is not None
                    and not getattr(profile, "supports_tool_choice", False)
                )
                if (
                    not requires_two_source_reads
                    or len(self.current_run_source_reads) >= 2
                ):
                    self.set_action_readiness(state, "action_expected", tool_step)
            elif state["action_readiness"] == "unknown":
                self.set_action_readiness(state, "evidence_gathering", tool_step)
        redundant = False
        if name == "search":
            signature = task_policy.normalize_search(args)
            redundant = signature in state["seen_searches"]
            state["seen_searches"].add(signature)
        elif name == "read_file":
            path = task_policy.canonical_path(args.get("path"))
            prior = state["seen_reads"].setdefault(path, [])
            redundant = any(
                task_policy.read_overlap_ratio(args, previous) >= READ_OVERLAP_THRESHOLD
                for previous in prior
            )
            prior.append(dict(args))
        if redundant:
            state["redundant_exploration_steps"] += 1
        else:
            state["productive_exploration_steps"] += 1
        return redundant

    def evidence_ledger_entries(self):
        return list(self.current_planning.get("evidence_ledger", []))

    def assess_read_evidence(self, args):
        """Classify a read against evidence rendered in the prompt that chose it."""
        if not self.current_planning or not self.last_prompt_metadata:
            return "new", None
        path = task_policy.canonical_path((args or {}).get("path"))
        current_freshness = memorylib.file_freshness(path, self.root)
        prompt_entries = (
            self.last_prompt_metadata.get("inspected_evidence") or {}
        ).get("entries", [])
        candidates = [
            entry
            for entry in prompt_entries
            if entry.get("path") == path
            and entry.get("freshness") == current_freshness
            and task_policy.read_overlap_ratio(args, entry) >= READ_OVERLAP_THRESHOLD
        ]
        if not candidates:
            return "new", None
        visible = any(bool(entry.get("visible")) for entry in candidates)
        return (
            "avoidable_repeated_read" if visible else "evidence_evicted_reread"
        ), candidates[-1]

    def read_guard_notice(self, args):
        classification, entry = self.assess_read_evidence(args)
        if classification != "avoidable_repeated_read" or entry is None:
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

    def compact_evidence_hint(self, result):
        lines = [line.strip() for line in str(result).splitlines() if line.strip()]
        if lines and lines[0].startswith("# "):
            lines = lines[1:]
        hint = " | ".join(lines[:8])
        hint = self.redact_text(hint)
        hint = re.sub(
            r"(?i)((?:api[_ -]?key|token|secret|password)\s*[:=]\s*[\"']?)[^\s,\"']+",
            r"\1<redacted>",
            hint,
        )
        return clip(hint, EVIDENCE_HINT_LIMIT)

    def record_read_evidence(self, args, result, tool_step):
        path = task_policy.canonical_path((args or {}).get("path"))
        if not path:
            return
        entry = {
            "path": path,
            "start": int((args or {}).get("start", 1)),
            "end": int((args or {}).get("end", 200)),
            "freshness": memorylib.file_freshness(path, self.root),
            "last_read_step": tool_step,
            "hint": self.compact_evidence_hint(result),
        }
        entry["marker"] = hashlib.sha256(
            f"{entry['path']}:{entry['start']}:{entry['end']}:{entry['freshness']}".encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        ledger = self.current_planning["evidence_ledger"]
        ledger[:] = [
            prior
            for prior in ledger
            if not (
                prior["path"] == entry["path"]
                and prior["start"] == entry["start"]
                and prior["end"] == entry["end"]
                and prior["freshness"] == entry["freshness"]
            )
        ]
        ledger.append(entry)
        if len(ledger) > EVIDENCE_LEDGER_LIMIT:
            del ledger[: len(ledger) - EVIDENCE_LEDGER_LIMIT]
            self.current_planning["evidence_eviction_count"] += 1

    def invalidate_evidence_for_paths(self, paths):
        changed = {task_policy.canonical_path(path) for path in (paths or [])}
        if not changed:
            return
        ledger = self.current_planning.get("evidence_ledger", [])
        retained = [entry for entry in ledger if entry["path"] not in changed]
        self.current_planning["evidence_eviction_count"] += len(ledger) - len(retained)
        self.current_planning["evidence_ledger"] = retained

    def maybe_emit_exploration_warning(self, task_state):
        state = self.current_planning
        if state["warning_sent"]:
            return False
        if (
            state["consecutive_exploration"] < EXPLORATION_WARNING_THRESHOLD
            and state["redundant_exploration_steps"] < SEMANTIC_REPEAT_WARNING_THRESHOLD
        ):
            return False
        state["warning_sent"] = True
        state["exploration_warning_count"] += 1
        notice = (
            "Runtime planning notice: substantial repository exploration has occurred without an implementation action. "
            "Reassess whether further exploration is necessary. If evidence is sufficient, make the smallest justified change and verify it."
        )
        self.record({"role": "assistant", "content": notice, "created_at": now()})
        self.emit_trace(
            task_state,
            "exploration_warning",
            {
                "consecutive_exploration": state["consecutive_exploration"],
                "redundant_exploration_steps": state["redundant_exploration_steps"],
            },
        )
        return True

    def maybe_emit_implementation_warning(self, task_state):
        state = self.current_planning
        if (
            not self.requires_workspace_change
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
        self.record({"role": "assistant", "content": notice, "created_at": now()})
        self.emit_trace(
            task_state,
            "implementation_warning",
            {
                "verification_steps": state["verification_steps"],
                "workspace_change_count": state["workspace_change_count"],
            },
        )
        return True

    @staticmethod
    def new_task_id():
        return (
            "task_"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )

    @staticmethod
    def new_run_id():
        return (
            "run_"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )

    def cancellation_requested(self, task_state):
        checker = getattr(self, "cancel_checker", None)
        if checker is None:
            return False
        return bool(checker(self, task_state))

    def stop_user_canceled_run(self, task_state, run_started_wall, run_started_at):
        final = "Canceled by user."
        task_state.stop_user_canceled(final)
        self.run_store.write_task_state(task_state)
        self.emit_trace(
            task_state,
            "run_canceled",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        self.emit_run_status(
            task_state,
            "canceled",
            "Canceled",
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.write_final_report(task_state)
        return final

    def stop_model_error_run(
        self, task_state, exc, model_started_at, run_started_wall, run_started_at
    ):
        error_message = self.redact_text(str(exc))
        error_type = exc.__class__.__name__
        final = (
            f"Model error: {error_message}"
            if error_message
            else f"Model error: {error_type}"
        )
        self.last_model_error = {
            "error_type": error_type,
            "message": error_message,
        }
        task_state.stop_model_error(final)
        self.run_store.write_task_state(task_state)
        self.emit_trace(
            task_state,
            "model_error",
            {
                "error_type": error_type,
                "message": error_message,
                "duration_ms": int((time.monotonic() - model_started_at) * 1000),
            },
        )
        self.emit_run_status(
            task_state,
            "failed",
            "Failed",
            detail="model_error",
            started_at=run_started_wall,
            run_started_at=run_started_at,
        )
        self.emit_run_finished(task_state, final, run_started_at)
        self.write_final_report(task_state)
        return final

    @staticmethod
    def validate_external_run_id(run_id):
        value = str(run_id or "").strip()
        if value in {"", ".", ".."} or RUN_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "invalid run_id: use only letters, numbers, underscore, dash, and dot"
            )
        return value

    def build_report(self, task_state):
        # report 是一次运行的最终摘要；
        # 和 trace 的区别在于，trace 关注过程，report 关注结果与关键指标。
        return {
            "run_id": task_state.run_id,
            "task_id": task_state.task_id,
            "status": task_state.status,
            "stop_reason": task_state.stop_reason,
            "final_answer": task_state.final_answer,
            "tool_steps": task_state.tool_steps,
            "attempts": task_state.attempts,
            "checkpoint_id": task_state.checkpoint_id,
            "resume_status": task_state.resume_status,
            "task_state": task_state.to_dict(),
            "runtime_mode": self.runtime_mode,
            "effective_step_budget": self.effective_step_budget,
            "emergency_cap": (
                None
                if self.effective_step_budget is not None
                else int(self.emergency_cap or 0)
            ),
            "watchdog": self.watchdog.snapshot(),
            "edit_decision_watchdog": self.edit_decision_watchdog.snapshot(),
            "prompt_metadata": self.last_prompt_metadata,
            "usage_summary": aggregate_usage_records(self.current_run_usage),
            "planning": {
                key: value
                for key, value in self.current_planning.items()
                if key
                not in {
                    "seen_reads",
                    "seen_searches",
                    "seen_verifications",
                    "warning_sent",
                    "implementation_warning_sent",
                    "read_guard_notices",
                }
            },
            "usage_snapshot": build_usage_snapshot(
                self.current_run_usage,
                "run",
                session_id=self.session.get("id", ""),
                run_id=task_state.run_id,
            ),
            "durable_promotions": list(self.last_durable_promotions),
            "durable_rejections": list(self.last_durable_rejections),
            "durable_superseded": list(self.last_durable_superseded),
            "memory_v2": self.memory_v2.metrics() if self.memory_v2_enabled() else {},
            "memory_migration": (
                self.memory_v2.last_migration.to_dict()
                if self.memory_v2_enabled()
                and getattr(self.memory_v2, "last_migration", None) is not None
                else None
            ),
            "memory_v2_activity": {
                "promotions": list(self.last_memory_v2_promotions),
                "rejections": list(self.last_memory_v2_rejections),
                "superseded": list(self.last_memory_v2_superseded),
                "conflicts": list(self.last_memory_v2_conflicts),
            },
            "redacted_env": self.detected_secret_env_summary(),
        }

    def tool_example(self, name):
        return toolkit.tool_example(name)

    def validate_tool(self, name, args):
        """把通用工具校验和 runtime 级额外约束串起来。"""
        toolkit.validate_tool(self, name, args)
        if name == "delegate":
            if self.depth >= self.max_depth:
                raise ValueError("delegate depth exceeded")

    def tool_list_files(self, args):
        return toolkit.tool_list_files(self, args)

    def tool_read_file(self, args):
        return toolkit.tool_read_file(self, args)

    def tool_search(self, args):
        return toolkit.tool_search(self, args)

    def tool_run_shell(self, args):
        return toolkit.tool_run_shell(self, args)

    def tool_write_file(self, args):
        return toolkit.tool_write_file(self, args)

    def tool_patch_file(self, args):
        return toolkit.tool_patch_file(self, args)

    def tool_delegate(self, args):
        return toolkit.tool_delegate(self, args)

    def approve(self, name, args):
        if self.read_only:
            return False
        if self.approval_policy == "auto":
            return True
        if self.approval_policy == "never":
            return False
        if self.approval_policy == "ask" and self.approval_handler is not None:
            return bool(self.approval_handler(name, args, self))
        try:
            answer = input(
                f"approve {name} {json.dumps(args, ensure_ascii=True)}? [y/N] "
            )
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    @staticmethod
    def parse(raw):
        """把模型原始输出解析成 runtime 可执行的动作或最终答案。

        为什么存在：
        模型输出首先是自然语言文本，而 runtime 需要的是结构化决策：
        “这是工具调用”还是“这是最终答案”。如果没有这层解析，后面的工具校验、
        审批和执行链路就没法可靠工作。

        输入 / 输出：
        - 输入：模型返回的原始文本 `raw`
        - 输出：`(kind, payload)`，其中 `kind` 可能是 `tool`、`final`、`retry`

        在 agent 链路里的位置：
        它位于 `model_client.complete()` 之后、`run_tool()` 之前，是模型输出
        进入平台控制流的第一道结构化关口。
        """
        raw = str(raw)
        # 这里支持两种工具格式：
        # 1. <tool>...</tool> 里包 JSON，适合简短调用
        # 2. XML 风格属性/子标签，适合写文件这类多行内容
        if "<tool>" in raw and (
            "<final>" not in raw or raw.find("<tool>") < raw.find("<final>")
        ):
            body = Pico.extract(raw, "tool")
            try:
                payload = json.loads(body)
            except Exception:
                return "retry", Pico.retry_notice("model returned malformed tool JSON")
            if not isinstance(payload, dict):
                return "retry", Pico.retry_notice("tool payload must be a JSON object")
            if not str(payload.get("name", "")).strip():
                return "retry", Pico.retry_notice("tool payload is missing a tool name")
            args = payload.get("args", {})
            if args is None:
                payload["args"] = {}
            elif not isinstance(args, dict):
                return "retry", Pico.retry_notice()
            return "tool", payload
        if "<tool" in raw and (
            "<final>" not in raw or raw.find("<tool") < raw.find("<final>")
        ):
            payload = Pico.parse_xml_tool(raw)
            if payload is not None:
                return "tool", payload
            return "retry", Pico.retry_notice()
        if "<final>" in raw:
            final = Pico.extract(raw, "final").strip()
            if final:
                return "final", final
            return "retry", Pico.retry_notice("model returned an empty <final> answer")
        raw = raw.strip()
        if raw:
            return "retry", Pico.retry_notice(
                "model response is missing required <tool> or <final> tags"
            )
        return "retry", Pico.retry_notice("model returned an empty response")

    @staticmethod
    def retry_notice(problem=None):
        prefix = "Runtime notice"
        if problem:
            prefix += f": {problem}"
        else:
            prefix += ": model returned malformed tool output"
        return (
            f"{prefix}. Reply with a valid <tool> call or a non-empty <final> answer. "
            'For multi-line files, prefer <tool name="write_file" path="file.py"><content>...</content></tool>.'
        )

    @staticmethod
    def parse_xml_tool(raw):
        match = re.search(r"<tool(?P<attrs>[^>]*)>(?P<body>.*?)</tool>", raw, re.S)
        if not match:
            return None
        attrs = Pico.parse_attrs(match.group("attrs"))
        name = str(attrs.pop("name", "")).strip()
        if not name:
            return None

        body = match.group("body")
        args = dict(attrs)
        for key in (
            "content",
            "old_text",
            "new_text",
            "command",
            "task",
            "pattern",
            "path",
        ):
            if f"<{key}>" in body:
                args[key] = Pico.extract_raw(body, key)

        body_text = body.strip("\n")
        if name == "write_file" and "content" not in args and body_text:
            args["content"] = body_text
        if name == "delegate" and "task" not in args and body_text:
            args["task"] = body_text.strip()
        return {"name": name, "args": args}

    @staticmethod
    def parse_attrs(text):
        attrs = {}
        for match in re.finditer(
            r"""([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""", text
        ):
            attrs[match.group(1)] = (
                match.group(2) if match.group(2) is not None else match.group(3)
            )
        return attrs

    @staticmethod
    def extract(text, tag):
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:].strip()
        return text[start:end].strip()

    @staticmethod
    def extract_raw(text, tag):
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:]
        return text[start:end]

    def reset(self):
        self.session["history"] = []
        self.session["memory"].clear()
        self.session["memory"].update(memorylib.default_memory_state())
        self.memory = memorylib.LayeredMemory(
            self.session["memory"], workspace_root=self.root
        )
        if self.memory_v2_enabled():
            self.memory_v2.reset_run_state()
        self.session_store.save(self.session)

    def path(self, raw_path):
        path = Path(raw_path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        # 所有文件类工具都被锚定在 workspace root 之下。
        # 这样既能防住 "../" 逃逸，也能防住符号链接解析后跳出仓库。
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved


MiniAgent = Pico
