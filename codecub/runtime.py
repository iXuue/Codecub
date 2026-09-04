"""Pico composition root and public compatibility facade.

The model/tool reasoning loop lives in :mod:`codecub.agent.loop`; this module
assembles its collaborators and preserves the historical ``Pico`` API.
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
from .context_assembler import ContextAssembler
from .context_validator import ContextValidator
from .instruction_loader import InstructionLoader
from .instructions import InstructionResolver
from .context_compiler import (
    ContextBudget,
    ContextCompiler,
    HistoryCondenser,
    WorkingState,
)
from .code_index import CodeIndex
from .edit_decision import EditDecisionWatchdog
from .token_budget import resolve_token_counter
from .run_store import RunStore
from .sessions import SessionManager, SessionStore as _SessionStore
from .agent import AgentLoop, LegacyContextAdapter, LegacyLoopStateAdapter, LegacyModelInvoker, LoopHistory, LoopObserver, LoopStatus, TurnPreparation, TurnRunner
from .agent.hooks import HookComposite
from .tooling import ToolExecutionContext, ToolExecutor
from .models import ToolCall
from .telemetry import aggregate_usage_records, build_usage_snapshot

from .usage_store import UsageStore
from .task_state import TaskState
from . import tools as toolkit
from . import task_policy
from .watchdog import ProgressWatchdog
from .resilience import ToolCircuitBreaker
from .sandbox import WorkspaceBoundarySandbox
from .retrieval import HybridRetriever
from .event_bus import LocalEventBus
from .cache import LocalJsonCache, file_summary_cache_key
from .orchestration import Orchestrator
from .workspace import IGNORED_PATH_NAMES, MAX_HISTORY, WorkspaceContext, clip, now
from .auth import CapabilityPolicy

# Public compatibility export; callers historically import this from runtime.
SessionStore = _SessionStore

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
    # Adaptive Hybrid Raw Evidence is experimental; production uses
    # State-Preserving Compression by default.
    "hybrid_context_enabled": False,
    "context_compiler": True,
    "prompt_cache": True,
}
DEFAULT_MAX_STEPS = 80
DEFAULT_INTERACTIVE_EMERGENCY_CAP = 500
DEFAULT_INTERACTIVE_ATTEMPT_CAP = 1200
RUNTIME_MODE_INTERACTIVE = "interactive"
RUNTIME_MODE_EXPERIMENT = "experiment"
EXECUTION_MODE_SINGLE = "single"
EXECUTION_MODE_MULTI_AGENT = "multi_agent"
EXECUTION_MODES = {EXECUTION_MODE_SINGLE, EXECUTION_MODE_MULTI_AGENT}
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
READ_RANGE_LEDGER_LIMIT = 20
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


@dataclass(frozen=True)
class ReadRangeRecord:
    path: str
    start_line: int
    end_line: int
    freshness: str
    step: int


@dataclass(frozen=True)
class ReadCoverage:
    covered_ranges: tuple[tuple[int, int], ...]
    uncovered_ranges: tuple[tuple[int, int], ...]
    previous_step: int = 0

    @property
    def fully_covered(self):
        return not self.uncovered_ranges


class _RuntimeToolSubject:
    """Explicit capability view used to bind the canonical tool functions."""

    def __init__(self, *, root, workspace, session_store, run_store, model_client,
                 model_gateway, max_new_tokens, depth, max_depth, read_only,
                 execution_mode,
                 secret_env_names, shell_env_allowlist, code_index, retriever,
                 orchestrator, cancellation, state_ref, working_state_ref,
                 session, cancel_checker_ref):
        self.root = root
        self.workspace = workspace
        self.session_store = session_store
        self.run_store = run_store
        self.model_client = model_client
        self.model_gateway = model_gateway
        self.max_new_tokens = max_new_tokens
        self.depth = depth
        self.max_depth = max_depth
        self.read_only = read_only
        self.execution_mode = execution_mode
        self.secret_env_names = secret_env_names
        self.shell_env_allowlist = shell_env_allowlist
        self.code_index = code_index
        self.retriever = retriever
        self.orchestrator = orchestrator
        self.cancellation_source = cancellation
        self._state_ref = state_ref
        self._working_state_ref = working_state_ref
        self.session = session
        self._cancel_checker_ref = cancel_checker_ref
        self.last_retrieval_result = None

    @property
    def current_task_state(self):
        return self._state_ref.get("current")

    @property
    def working_state(self):
        return self._working_state_ref.get("current")

    @property
    def cancel_checker(self):
        return self._cancel_checker_ref.get("value")

    def path(self, raw_path):
        path = Path(raw_path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved

    def multi_agent_enabled(self):
        return self.execution_mode == EXECUTION_MODE_MULTI_AGENT

    def cancellation_requested(self, task_state=None):
        return self.cancellation_source.requested(task_state)

    def shell_env(self):
        env = {name: os.environ[name] for name in self.shell_env_allowlist if name in os.environ}
        env["PWD"] = str(self.root)
        env["GIT_DIR"] = os.devnull
        if "PATH" not in env and os.environ.get("PATH"):
            env["PATH"] = os.environ["PATH"]
        if os.name == "nt":
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            env.setdefault("SystemRoot", system_root)
            env.setdefault("ComSpec", os.environ.get("ComSpec", str(Path(system_root) / "System32" / "cmd.exe")))
        return env

    def history_text(self):
        history = self.session.get("history", [])
        if not history:
            return "- empty"
        return clip("\n".join(str(item.get("content", "")) for item in history), MAX_HISTORY)


class _RuntimeToolRegistry:
    """Registry seam: the executor sees lookup, not Pico's private map."""

    def __init__(self, tools):
        self._tools = tools

    def resolve(self, name):
        return self._tools.get(name)


class _RuntimeToolValidation:
    def __init__(self, *, root, depth, max_depth):
        self.root = root
        self.depth = depth
        self.max_depth = max_depth

    def path(self, raw_path):
        path = Path(raw_path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved

    def validate(self, name, args, _tool):
        toolkit.validate_tool(self, name, args)
        if str(name).startswith("mcp_"):
            toolkit.validate_json_tool_arguments(_tool, args)

    def example(self, name):
        return toolkit.tool_example(name)

    @staticmethod
    def validate_result(tool, result):
        return toolkit.validate_tool_result_contract(tool, result)


class _RuntimeToolApproval:
    def __init__(self, *, read_only, approval_policy, approval_handler_ref, subject):
        self._read_only = bool(read_only)
        self._approval_policy = approval_policy
        self._approval_handler_ref = approval_handler_ref
        self._subject = subject

    @property
    def read_only(self):
        return self._read_only

    def approve(self, name, args):
        if self._read_only or self._approval_policy == "never":
            return False
        if self._approval_policy == "auto":
            return True
        handler = self._approval_handler_ref.get("value")
        if self._approval_policy == "ask" and handler is not None:
            return bool(handler(name, args, self._subject))
        try:
            answer = input(f"approve {name} {json.dumps(args, ensure_ascii=True)}? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}


class _RuntimeToolReplay:
    def __init__(self, *, tools, circuit_breaker, run_store, state_ref):
        self._tools = tools
        self._circuit_breaker = circuit_breaker
        self._run_store = run_store
        self._state_ref = state_ref
        self.limit = REPEATED_NO_PROGRESS_LIMIT

    def repeated(self, name, args):
        history = self._state_ref.get("history", [])
        required = self.limit - 1
        if len(history) < required:
            return False
        recent = history[-required:]
        return all(item.get("name") == name and item.get("args") == args for item in recent)

    def allow(self, name):
        return self._circuit_breaker.allow(name)

    def status(self, name):
        return self._circuit_breaker.status(name)

    def claim(self, name, args, operation_key, side_effect):
        task_state = self._state_ref.get("current")
        if not side_effect or not operation_key or task_state is None:
            return {"claimed": True}
        digest = hashlib.sha256(json.dumps(args or {}, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        claim = self._run_store.claim_side_effect_operation(task_state, operation_key, name, digest)
        if claim["claimed"]:
            return {"claimed": True, "operation_key": operation_key, "args_digest": digest}
        prior_state = claim.get("prior_state", "")
        if claim.get("conflict"):
            code, message = "idempotency_key_conflict", "error: idempotency key conflicts with a different side-effect operation"
        elif prior_state == "completed":
            code, message = "side_effect_replay_blocked", "error: side-effect operation was already completed"
        else:
            code, message = "outcome_uncertain", "error: prior side-effect outcome is uncertain; replay is blocked"
        return {
            "claimed": False, "error_code": code, "message": message,
            "metadata": {
                "side_effect_operation_key": operation_key, "side_effect_args_digest": digest,
                "side_effect_claimed": False, "side_effect_replay_detected": True,
                "side_effect_replay_blocked": True, "side_effect_prior_state": prior_state,
                "side_effect_commit_recorded": False,
                "side_effect_outcome_uncertain": prior_state in {"claimed", "uncertain"},
                "idempotency_key_conflict": bool(claim.get("conflict")),
            },
        }

    def complete(self, claim, success, metadata):
        key = claim.get("operation_key", "")
        task_state = self._state_ref.get("current")
        if not key or task_state is None:
            return
        metadata.update({
            "side_effect_operation_key": key, "side_effect_args_digest": claim["args_digest"],
            "side_effect_claimed": True, "side_effect_replay_detected": False,
            "side_effect_replay_blocked": False, "side_effect_prior_state": "",
            "side_effect_commit_recorded": bool(success),
            "side_effect_outcome_uncertain": not bool(success), "idempotency_key_conflict": False,
        })
        self._run_store.update_side_effect_operation(
            task_state, key, "completed" if success else "uncertain",
            {key: metadata.get(key) for key in (
                "tool_status", "tool_error_code", "tool_execution_success",
                "tool_business_success", "tool_result_validation_failed",
                "tool_result_failure_reason", "workspace_changed",
            )},
        )

    def record_result(self, name, success):
        if self._tools.get(name, {}).get("circuit_breaker", True):
            recorder = self._circuit_breaker.record_success if success else self._circuit_breaker.record_failure
            recorder(name)


class _RuntimeToolCancellation:
    def __init__(self, checker_ref, state_ref):
        self._checker_ref = checker_ref
        self._state_ref = state_ref

    def requested(self, task_state=None):
        state = task_state or self._state_ref.get("current")
        checker = self._checker_ref.get("value")
        return bool(checker is not None and checker(self, state))


class _RuntimeToolWorkspace:
    def __init__(self, *, root):
        self._root = root

    def snapshot(self):
        snapshot = {}
        for path in self._root.rglob("*"):
            if not path.is_file() or any(part in IGNORED_PATH_NAMES for part in path.relative_to(self._root).parts):
                continue
            try:
                snapshot[path.relative_to(self._root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            except Exception:
                continue
        return snapshot

    @staticmethod
    def diff(before, after):
        changed, summaries = [], []
        for path in sorted(set(before) | set(after)):
            if before.get(path) == after.get(path):
                continue
            changed.append(path)
            summaries.append(
                f"created:{path}" if path not in before else f"deleted:{path}" if path not in after else f"modified:{path}"
            )
        return changed, summaries

    def fingerprint(self):
        return WorkspaceContext.build(self._root).fingerprint()


class _RuntimeToolObservation:
    """Explicit tool-result sink for read ranges, memory, and code index state."""

    def __init__(self, *, root, state_ref, planning_ref, working_state_ref,
                 task_state_ref, metadata_ref, memory, memory_v2, feature_flags,
                 code_index, file_summary_cache, model_client, session):
        self._root = root
        self._state_ref = state_ref
        self._planning_ref = planning_ref
        self._working_state_ref = working_state_ref
        self._task_state_ref = task_state_ref
        self._metadata_ref = metadata_ref
        self._memory = memory
        self._memory_v2 = memory_v2
        self._feature_flags = feature_flags
        self._code_index = code_index
        self._file_summary_cache = file_summary_cache
        self._model_client = model_client
        self._session = session
        self.last_metadata = {}

    @property
    def planning(self):
        state = self._state_ref.get("current")
        return getattr(state, "planning", None) or self._planning_ref.get("current", {})

    @property
    def task_state(self):
        return self._task_state_ref.get("current")

    def _assess_read_range(self, args):
        path = task_policy.canonical_path((args or {}).get("path"))
        if not path:
            return None, ""
        freshness = memorylib.file_freshness(path, self._root)
        coverage = analyze_read_coverage(
            path, int((args or {}).get("start", 1)), int((args or {}).get("end", 200)),
            self.planning.get("read_range_ledger", []), freshness,
        )
        return coverage, freshness

    @staticmethod
    def _redundant_read_notice(args, coverage, action):
        path = task_policy.canonical_path((args or {}).get("path"))
        start, end = int((args or {}).get("start", 1)), int((args or {}).get("end", 200))
        covered = ", ".join(f"L{item_start}-L{item_end}" for item_start, item_end in coverage.covered_ranges)
        return (
            f"Read guard: {path} L{start}-L{end} is already covered by previous read evidence ({covered}) at step {coverage.previous_step}; unchanged=true. "
            f"No source was returned; if raw source is specifically needed, call read_file(..., force=True). action={action}."
        )

    def prepare(self, name, args, _tool):
        if name != "read_file":
            return {}
        planning = self.planning
        coverage, version = self._assess_read_range(args)
        covered_lines = sum(end - start + 1 for start, end in coverage.covered_ranges)
        base_metadata = {"security_event_type": "", "risk_level": "low", "read_only": True, "affected_paths": [], "workspace_changed": False, "diff_summary": []}
        if coverage.covered_ranges and not bool(args.get("force", False)) and coverage.fully_covered:
            planning["read_guard_triggered"] += 1
            planning["redundant_read_suppressed"] += 1
            planning["read_guard_covered_lines_skipped"] += covered_lines
            return {"result": self._redundant_read_notice(args, coverage, "suppressed"), "metadata": {**base_metadata, "read_range_guard": {
                "path": task_policy.canonical_path(args.get("path")),
                "requested_range": [int(args.get("start", 1)), int(args.get("end", 200))],
                "covered_ranges": [list(item) for item in coverage.covered_ranges], "returned_ranges": [],
                "file_changed": False, "action": "suppressed", "file_version": version,
            }}}
        if coverage.covered_ranges and not bool(args.get("force", False)):
            planning["read_guard_triggered"] += 1
            planning["read_guard_delta_reads"] += 1
            planning["read_guard_delta_lines_returned"] += sum(end - start + 1 for start, end in coverage.uncovered_ranges)
            planning["read_guard_covered_lines_skipped"] += covered_lines
            covered = ", ".join(f"L{start}-L{end}" for start, end in coverage.covered_ranges)
            returned = ", ".join(f"L{start}-L{end}" for start, end in coverage.uncovered_ranges)
            return {
                "calls": tuple({**args, "start": start, "end": end} for start, end in coverage.uncovered_ranges),
                "format_result": lambda values: f"Read guard: {covered} was already available and unchanged. Only previously unseen {returned} is returned below.\n\n" + "\n\n".join(values),
                "action": "delta_read", "coverage": coverage, "version": version,
            }
        action = "forced" if bool(args.get("force", False)) and coverage.covered_ranges else "read"
        if action == "forced":
            planning["read_guard_triggered"] += 1
            planning["redundant_read_forced"] += 1
        elif any((item.path if isinstance(item, ReadRangeRecord) else item["path"]) == task_policy.canonical_path(args.get("path")) for item in planning.get("read_range_ledger", [])):
            action = "stale_read"
        return {"action": action, "coverage": coverage, "version": version}

    def _record_read_range(self, args, step):
        path = task_policy.canonical_path((args or {}).get("path"))
        if not path:
            return
        record = ReadRangeRecord(path, int((args or {}).get("start", 1)), int((args or {}).get("end", 200)), memorylib.file_freshness(path, self._root), int(step or 0))
        ledger = self.planning.setdefault("read_range_ledger", [])
        previous = [item if isinstance(item, ReadRangeRecord) else ReadRangeRecord(**item) for item in ledger]
        ledger[:] = [{"path": item.path, "start_line": item.start_line, "end_line": item.end_line, "freshness": item.freshness, "step": item.step} for item in merge_read_ranges(previous, record)[-20:]]
        self.planning["read_range_ledger_entries"] = len(ledger)

    def finalize(self, name, args, _result, metadata, prepared):
        if name != "read_file":
            return
        coverage = prepared.get("coverage")
        action = prepared.get("action", "read")
        metadata["read_range_guard"] = {
            "path": task_policy.canonical_path(args.get("path")),
            "requested_range": [int(args.get("start", 1)), int(args.get("end", 200))],
            "covered_ranges": [list(item) for item in coverage.covered_ranges] if coverage else [],
            "returned_ranges": [list(item) for item in coverage.uncovered_ranges] if action == "delta_read" and coverage else [[int(args.get("start", 1)), int(args.get("end", 200))]],
            "file_changed": action == "stale_read", "action": action, "file_version": prepared.get("version", ""),
        }
        self._record_read_range(args, getattr(self.task_state, "tool_steps", 0))

    def _update_memory_after_tool(self, name, args, result):
        if not self._feature_flags.get("memory", False) or not args.get("path"):
            return
        canonical_path = self._memory.canonical_path(args["path"])
        if name in {"read_file", "write_file", "patch_file"}:
            self._memory.remember_file(canonical_path)
        if name == "read_file":
            try:
                content_hash = hashlib.sha256((self._root / str(args["path"])).resolve().read_bytes()).hexdigest()
            except OSError:
                content_hash = ""
            key = file_summary_cache_key(canonical_path, content_hash, getattr(self._model_client, "model", "local"), "read-summary-v1")
            summary = self._file_summary_cache.get(key)
            if summary is None:
                summary = memorylib.summarize_read_result(result)
                self._file_summary_cache.set(key, summary)
            self._memory.set_file_summary(canonical_path, summary)
            self._memory.append_note(summary, tags=(canonical_path,), source=canonical_path)
        elif name in {"write_file", "patch_file"}:
            self._memory.invalidate_file_summary(canonical_path)

    def _record_memory_v2_evidence(self, name, args, result, metadata):
        if not (self._feature_flags.get("memory", False) and self._feature_flags.get("memory_v2", False) and self._feature_flags.get("evidence_memory", False)):
            return
        try:
            self._memory_v2.record_tool_evidence(name, args, result, metadata=metadata)
            if name == "read_file":
                self._memory_v2.note_read(str(args.get("path") or ""))
        except Exception:
            return

    def _record_process_note(self, name, metadata):
        status = str(metadata.get("tool_status", "")).strip()
        if status not in {"partial_success", "error", "rejected"}:
            return
        affected = [str(path).strip() for path in metadata.get("affected_paths", []) if str(path).strip()]
        path_text = ", ".join(affected) or "workspace"
        detail = "inspect diff before retry" if status == "partial_success" else "check the failure before retry" if status == "error" else "choose a different action before retry"
        self._memory.append_note(f"{name} {status} on {path_text}; {detail}", tags=("process", status, *affected), source=name, kind="process")
        self._session["memory"] = self._memory.to_dict()

    def record(self, name, args, result, metadata):
        self.last_metadata = dict(metadata)
        target = self._metadata_ref.setdefault("value", {})
        target.clear()
        target.update(self.last_metadata)
        if metadata.get("workspace_changed"):
            self.planning["read_range_ledger_entries"] = len(self.planning.get("read_range_ledger", []))
            self.last_metadata["code_index_refresh"] = self._code_index.refresh(metadata.get("affected_paths", []))
            target.clear()
            target.update(self.last_metadata)
        if metadata.get("tool_execution_success"):
            self._update_memory_after_tool(name, args, result)
            self._record_memory_v2_evidence(name, args, result, self.last_metadata)
        self._record_process_note(name, self.last_metadata)


class _RuntimeTurnLifecycle:
    """Narrow port exposing Runtime-owned primitives to TurnRunner.

    TurnRunner owns terminal ordering and outcome mapping.  This temporary
    adapter only supplies legacy storage, event, memory, and session actions
    that have not yet moved out of Runtime.
    """

    def __init__(self, pico, run_store):
        self._pico = pico
        self._run_store = run_store

    @property
    def hook_subject(self):
        return self._pico

    def initialize(self, user_message, run_id):
        pico = self._pico
        run_started_at = time.monotonic()
        run_started_wall = now()
        pico.memory.set_task_summary(user_message)
        pico.record({"role": "user", "content": user_message, "created_at": now()})
        task_run_id = pico.validate_external_run_id(run_id) if run_id else pico.new_run_id()
        task_state = TaskState.create(
            run_id=task_run_id, task_id=pico.new_task_id(), user_request=user_message
        )
        state_path = self._run_store.task_state_path(task_run_id)
        if state_path.exists():
            task_state.side_effect_operations = dict(
                self._run_store.load_task_state(task_run_id).get("side_effect_operations", {}) or {}
            )
        elif pico.current_checkpoint():
            task_state.side_effect_operations = dict(
                pico.current_checkpoint().get("side_effect_operations", {}) or {}
            )
        task_state.resume_status = pico.resume_state.get("status", CHECKPOINT_NONE_STATUS)
        pico.current_task_state = task_state
        pico._tool_state_ref["current"] = task_state
        pico._tool_state_ref["history"] = pico.session["history"]
        pico.loop_history.begin_turn()
        pico.current_run_usage = pico.loop_history.run_usage
        pico.current_run_source_reads = pico.loop_history.source_reads
        pico.current_planning = pico.new_planning_state()
        if pico.working_state is not None:
            pico.working_state = WorkingState()
            pico.working_state.set_goal(user_message)
        pico._working_state_ref["current"] = pico.working_state
        pico._planning_state_ref["current"] = pico.current_planning
        reset_watchdogs = getattr(pico.loop_state_collaborator, "reset_watchdogs", None)
        if reset_watchdogs is not None:
            reset_watchdogs()
            pico.watchdog = pico.loop_state_collaborator.watchdog
            pico.edit_decision_watchdog = pico.loop_state_collaborator.edit_decision_watchdog
        pico.loop_state_collaborator.bind_turn(
            working_state=pico.working_state,
            planning=pico.current_planning,
            watchdog=pico.watchdog,
        )
        if pico.context_compiler is not None:
            pico.context_compiler.reset_run_state()
        if pico.memory_v2_enabled():
            pico.memory_v2.reset_run_state()
            pico.memory_v2.set_run_context(task_id=task_state.task_id, run_id=task_state.run_id)
        pico.current_run_dir = self._run_store.start_run(task_state)
        pico.emit_run_status(task_state, "building_context", "Building context", started_at=run_started_wall, run_started_at=run_started_at)
        pico.emit_trace(task_state, "run_started", {"task_id": task_state.task_id, "user_request": clip(user_message, 300)})
        if pico.memory_v2_enabled():
            pico.context_collaborator.refresh_memory_retrieval(user_message, force=True)
        return TurnPreparation(task_state, run_started_at, run_started_wall)

    def cancellation_requested(self):
        state = self._pico.current_task_state
        return bool(state and self._pico.cancellation_requested(state))

    def bind_loop(self, loop):
        pico = self._pico
        bind_context_state = getattr(pico.context_collaborator, "bind_loop_state", None)
        if bind_context_state is not None:
            bind_context_state(
                pico.loop_state_collaborator,
                working_state=pico.working_state,
                planning=pico.current_planning,
            )
        loop.bind_collaborators(
            context=pico.context_collaborator,
            model_invoker=pico.model_invoker,
            tool_executor=pico.tool_executor,
            loop_state=pico.loop_state_collaborator,
            injection_source=pico.injection_provider,
            cancellation=pico.cancellation_source,
            history=pico.loop_history,
            run_store=pico.run_store,
            status=pico.loop_status,
        )
        loop.bind_loop_config(prefix=pico.prefix)

    def emit_status(self, task_state, phase, label, outcome, detail=""):
        self._pico.emit_run_status(
            task_state, phase, label, detail=detail,
            started_at=outcome.started_wall, run_started_at=outcome.started_at,
        )

    def record_assistant(self, answer):
        self._pico.record({"role": "assistant", "content": answer, "created_at": now()})

    def enrich_memory(self, outcome):
        self._pico.promote_durable_memory(outcome.user_message, outcome.answer)
        self._pico.extract_memory_v2(outcome.user_message, outcome.answer)

    def create_checkpoint(self, task_state, user_message, trigger):
        return self._pico.create_checkpoint(task_state, user_message, trigger)

    def write_task_state(self, task_state):
        self._run_store.write_task_state(task_state)

    def emit_checkpoint_created(self, task_state, trigger):
        checkpoint = self._pico.current_checkpoint()
        self._pico.emit_checkpoint_created(task_state, checkpoint, trigger)

    def emit_run_finished(self, task_state, outcome):
        self._pico.emit_run_finished(task_state, outcome.answer, outcome.started_at)

    def write_final_report(self, task_state):
        self._pico.write_final_report(task_state)

    def record_model_error(self, outcome):
        self._pico.last_model_error = {
            "error_type": outcome.metadata["error_type"],
            "message": outcome.metadata["error_message"],
        }

    def emit_model_error(self, task_state, outcome):
        self._pico.emit_trace(
            task_state,
            "model_error",
            {
                "error_type": outcome.metadata["error_type"],
                "message": outcome.metadata["error_message"],
                "duration_ms": int(
                    (time.monotonic() - outcome.metadata["model_started_at"]) * 1000
                ),
            },
        )

    def emit_cancelled(self, task_state, outcome):
        self._pico.emit_trace(
            task_state,
            "run_canceled",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": outcome.answer,
                "run_duration_ms": int((time.monotonic() - outcome.started_at) * 1000),
            },
        )

    def emit_emergency_cap(self, task_state, outcome):
        self._pico.emit_trace(
            task_state,
            "emergency_cap_reached",
            {"cap": outcome.metadata["cap"], "tool_steps": task_state.tool_steps},
        )


def analyze_read_coverage(path, start, end, previous_ranges, current_file_version):
    """Compute union coverage for a requested range at one file freshness."""

    canonical = task_policy.canonical_path(path)
    matched = []
    previous_step = 0
    for raw_record in previous_ranges:
        record = raw_record if isinstance(raw_record, ReadRangeRecord) else ReadRangeRecord(**raw_record)
        if record.path != canonical or record.freshness != current_file_version:
            continue
        overlap_start, overlap_end = max(int(start), record.start_line), min(int(end), record.end_line)
        if overlap_start <= overlap_end:
            matched.append((overlap_start, overlap_end))
            previous_step = max(previous_step, record.step)
    covered = []
    for range_start, range_end in sorted(matched):
        if covered and range_start <= covered[-1][1] + 1:
            covered[-1] = (covered[-1][0], max(covered[-1][1], range_end))
        else:
            covered.append((range_start, range_end))
    uncovered, cursor = [], int(start)
    for range_start, range_end in covered:
        if cursor < range_start:
            uncovered.append((cursor, range_start - 1))
        cursor = max(cursor, range_end + 1)
    if cursor <= int(end):
        uncovered.append((cursor, int(end)))
    return ReadCoverage(tuple(covered), tuple(uncovered), previous_step)


def read_overlap_ratio(start, end, previous_start, previous_end):
    intersection = max(0, min(int(end), int(previous_end)) - max(int(start), int(previous_start)) + 1)
    smaller_range = min(int(end) - int(start) + 1, int(previous_end) - int(previous_start) + 1)
    return intersection / smaller_range if smaller_range > 0 else 0.0


def is_redundant_read(path, start, end, previous_ranges, current_file_version):
    """Return the covering record when an unchanged range overlaps by at least 80%."""

    canonical = task_policy.canonical_path(path)
    for raw_record in previous_ranges:
        record = (
            raw_record
            if isinstance(raw_record, ReadRangeRecord)
            else ReadRangeRecord(**raw_record)
        )
        if (
            record.path == canonical
            and record.freshness == current_file_version
            and read_overlap_ratio(start, end, record.start_line, record.end_line)
            >= READ_OVERLAP_THRESHOLD
        ):
            return record
    return None


def merge_read_ranges(records, new_record):
    """Merge overlapping/touching records for one file revision, preserving latest step."""

    compatible = [
        record
        for record in records
        if record.path == new_record.path and record.freshness == new_record.freshness
    ]
    retained = [record for record in records if record not in compatible]
    start, end, step = new_record.start_line, new_record.end_line, new_record.step
    pending = sorted(compatible, key=lambda item: (item.start_line, item.end_line))
    for record in pending:
        if record.end_line + 1 < start or record.start_line - 1 > end:
            retained.append(record)
            continue
        start, end = min(start, record.start_line), max(end, record.end_line)
        step = max(step, record.step)
    retained.append(ReadRangeRecord(new_record.path, start, end, new_record.freshness, step))
    return sorted(retained, key=lambda item: (item.step, item.path, item.start_line))


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
        execution_mode=EXECUTION_MODE_SINGLE,
        emergency_cap=None,
        event_bus=None,
        model_gateway=None,
        runtime_hooks=None,
        context_validator=None,
        instruction_resolver=None,
        instruction_loader=None,
        agent_role="",
        repository_id="",
        user_instructions=(),
        repository_instructions=(),
        agent_instructions=(),
        tool_instructions=(),
        capability_policy=None,
        runtime_identity=None,
    ):
        self.model_client = model_client
        self.model_gateway = model_gateway
        self.workspace = workspace
        self.root = Path(workspace.repo_root)
        self.sandbox = WorkspaceBoundarySandbox(self.root)
        self.file_summary_cache = LocalJsonCache(
            self.root / ".codecub" / "cache" / "file_summaries.json"
        )
        self.session_store = session_store
        self.session_manager = SessionManager(
            session_store, workspace.repo_root, memorylib.default_memory_state
        )
        self.approval_policy = approval_policy
        # max_steps=None 表示“不设固定步数预算”：interactive 模式由
        # Progress Watchdog + emergency cap 决定何时停止；experiment 模式
        # 由 ExperimentRunner 显式传入 task.step_budget。
        self.max_steps = max_steps
        self.runtime_mode = (
            str(runtime_mode or RUNTIME_MODE_INTERACTIVE).strip()
            or RUNTIME_MODE_INTERACTIVE
        )
        self.execution_mode = self.normalize_execution_mode(execution_mode)
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
        self.capability_policy = capability_policy or CapabilityPolicy()
        self.runtime_identity = runtime_identity
        self.allowed_tools = None if allowed_tools is None else frozenset(allowed_tools)
        self.requires_workspace_change = bool(requires_workspace_change)
        self.agent_role = str(agent_role or "").strip()
        self.repository_id = str(repository_id or "").strip()
        self.user_instructions = tuple(user_instructions or ())
        self.repository_instructions = tuple(repository_instructions or ())
        self.agent_instructions = tuple(agent_instructions or ())
        self.tool_instructions = tuple(tool_instructions or ())
        self.instruction_resolver = instruction_resolver or InstructionResolver()
        self.instruction_loader = instruction_loader or InstructionLoader(self.root)
        self.shell_env_allowlist = tuple(
            shell_env_allowlist or DEFAULT_SHELL_ENV_ALLOWLIST
        )
        self.secret_env_names = {str(name).upper() for name in (secret_env_names or ())}
        self._approval_handler_ref = {"value": approval_handler}
        self._cancel_checker_ref = {"value": None}
        self._tool_state_ref = {"current": None, "history": []}
        self._planning_state_ref = {"current": {}}
        self._working_state_ref = {"current": None}
        self._tool_metadata_ref = {"value": {}}
        self._event_handler_ref = {"value": event_handler}
        self.approval_handler = approval_handler
        self.event_handler = event_handler
        self.event_bus = event_bus or LocalEventBus()
        self.cancel_checker = None
        # Compatibility seam for Spine InjectionMailbox.  It is deliberately a
        # callable so the legacy Runtime does not import or own Spine state.
        self.injection_provider = None
        self.protected_runtime_constraints = []
        # Filled by the Spine compatibility adapter for every dispatched turn.
        # Kept separate from Session so concurrent conversations cannot borrow
        # correlation data from one another.
        self.spine_trace_context = {}
        self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        if feature_flags:
            self.feature_flags.update(
                {str(key): bool(value) for key, value in feature_flags.items()}
            )
        self.run_store = run_store or RunStore(
            Path(workspace.repo_root) / ".codecub" / "runs"
        )
        self.session = self.session_manager.create(session, now=now())
        self._tool_state_ref["history"] = self.session["history"]
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
        self.retriever = HybridRetriever(self.root, self.code_index)
        self.orchestrator = Orchestrator(
            model_client=self.model_client,
            model_gateway=self.model_gateway,
            workspace=self.workspace,
            session_store=self.session_store,
            run_store=self.run_store,
            approval_policy=self.approval_policy,
            max_new_tokens=self.max_new_tokens,
            secret_env_names=self.secret_env_names,
            shell_env_allowlist=self.shell_env_allowlist,
            event_bus=self.event_bus,
            state_ref=self._tool_state_ref,
            cancel_checker_ref=self._cancel_checker_ref,
        )
        self.last_retrieval_result = None
        self.tool_circuit_breaker = ToolCircuitBreaker()
        self.cancellation_source = _RuntimeToolCancellation(
            self._cancel_checker_ref, self._tool_state_ref
        )
        self.tool_subject = _RuntimeToolSubject(
            root=self.root,
            workspace=self.workspace,
            session_store=self.session_store,
            run_store=self.run_store,
            model_client=self.model_client,
            model_gateway=self.model_gateway,
            max_new_tokens=self.max_new_tokens,
            depth=self.depth,
            max_depth=self.max_depth,
            read_only=self.read_only,
            execution_mode=self.execution_mode,
            secret_env_names=self.secret_env_names,
            shell_env_allowlist=self.shell_env_allowlist,
            code_index=self.code_index,
            retriever=self.retriever,
            orchestrator=self.orchestrator,
            cancellation=self.cancellation_source,
            state_ref=self._tool_state_ref,
            working_state_ref=self._working_state_ref,
            session=self.session,
            cancel_checker_ref=self._cancel_checker_ref,
        )
        self.tools = self.build_tools()
        # MCP is opt-in and lazy: no network/process is opened during the
        # normal Pico constructor, but discovered tools share this live
        # registry and therefore the canonical ToolExecutor path.
        self.mcp_manager = None
        self.hooks = HookComposite(runtime_hooks or ())
        self.tool_executor = ToolExecutor(
            ToolExecutionContext(
                registry=_RuntimeToolRegistry(self.tools),
                validation=_RuntimeToolValidation(
                    root=self.root, depth=self.depth, max_depth=self.max_depth
                ),
                approval=_RuntimeToolApproval(
                    read_only=self.read_only,
                    approval_policy=self.approval_policy,
                    approval_handler_ref=self._approval_handler_ref,
                    subject=self.tool_subject,
                ),
                replay=_RuntimeToolReplay(
                    tools=self.tools,
                    circuit_breaker=self.tool_circuit_breaker,
                    run_store=self.run_store,
                    state_ref=self._tool_state_ref,
                ),
                cancellation=self.cancellation_source,
                workspace=_RuntimeToolWorkspace(root=self.root),
                observation=_RuntimeToolObservation(
                    root=self.root,
                    state_ref=self._tool_state_ref,
                    planning_ref=self._planning_state_ref,
                    working_state_ref=self._working_state_ref,
                    task_state_ref=self._tool_state_ref,
                    metadata_ref=self._tool_metadata_ref,
                    memory=self.memory,
                    memory_v2=self.memory_v2,
                    feature_flags=self.feature_flags,
                    code_index=self.code_index,
                    file_summary_cache=self.file_summary_cache,
                    model_client=self.model_client,
                    session=self.session,
                ),
                hook_subject=self.tool_subject,
                authorization=self.capability_policy,
            ),
            self.hooks,
        )
        self.model_invoker = LegacyModelInvoker(self.completion_client, self.max_new_tokens)
        self.loop_history = LoopHistory(self.session, self.session_manager)
        self.loop_status = LoopStatus(
            event_handler_ref=self._event_handler_ref, subject=self.tool_subject
        )
        self.usage_store = UsageStore(self.root / ".codecub" / "usage")
        self.loop_observer = LoopObserver(
            run_store=self.run_store,
            event_bus=self.event_bus,
            session=self.session,
            trace_context=self.spine_trace_context,
            secret_values=[value for _, value in self.detected_secret_env_items()],
            usage_store=self.usage_store,
            event_sink=self.loop_status.emit_app_event,
        )
        self.loop_state_collaborator = LegacyLoopStateAdapter(
            root=self.root, observer=self.loop_observer
        )
        self.protected_runtime_constraints = self.loop_state_collaborator.protected_constraints
        self.prefix_state = self.build_prefix()
        self.prefix = self.prefix_state.text
        # Phase 2: Context Compiler（task-local Working State + 分层 Context）。
        self.working_state = WorkingState()
        self.context_compiler = self.build_context_compiler()
        self.context_validator = context_validator or ContextValidator(
            token_counter=self.token_counter,
            workspace_root=self.root,
            max_validation_attempts=1,
        )
        self.resume_state = self.evaluate_resume_state()
        self.session_path = self.session_manager.save(self.session)
        self.current_task_state = None
        self._tool_state_ref["current"] = self.current_task_state
        if (
            hasattr(self.model_gateway, "event_sink")
            and self.model_gateway.event_sink is None
        ):
            self.model_gateway.event_sink = self._emit_gateway_event
        self.current_run_dir = None
        self.current_run_usage = []
        self.current_run_source_reads = []
        self.current_planning = self.new_planning_state()
        self._planning_state_ref["current"] = self.current_planning
        self.watchdog = ProgressWatchdog(file_hash_fn=self._file_freshness)
        self.edit_decision_watchdog = EditDecisionWatchdog(
            file_hash_fn=self._file_freshness
        )
        self.loop_state_collaborator.bind_turn(
            working_state=self.working_state,
            planning=self.current_planning,
            watchdog=self.watchdog,
        )
        self._working_state_ref["current"] = self.working_state
        self.last_model_error = {}
        self.last_durable_promotions = []
        self.last_durable_rejections = []
        self.last_durable_superseded = []
        self._last_tool_result_metadata = self._tool_metadata_ref["value"]
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

        # Context construction is explicit: the loop receives one assembler
        # boundary and state ports, never this composition root.
        self.context_assembler = ContextAssembler(
            context_compiler=self.context_compiler,
        )
        self._loop_metadata = {
            "prompt": {},
            "completion": {},
            "compression_count": 0,
        }
        self.last_prompt_metadata = self._loop_metadata["prompt"]
        self.last_completion_metadata = self._loop_metadata["completion"]
        self.context_collaborator = LegacyContextAdapter(
            root=self.root,
            workspace=self.workspace,
            prefix_state=self.prefix_state,
            tools=self.tools,
            session=self.session,
            session_manager=self.session_manager,
            memory=self.memory,
            memory_v2=self.memory_v2,
            context_compiler=self.context_compiler,
            loop_state=self.loop_state_collaborator,
            model_client=self.model_client,
            token_counter=self.token_counter,
            approval_policy=self.approval_policy,
            read_only=self.read_only,
            runtime_mode=self.runtime_mode,
            execution_mode=self.execution_mode,
            effective_step_budget=self.effective_step_budget,
            max_steps=self.max_steps,
            emergency_cap=self.emergency_cap,
            context_window=self.context_window,
            max_new_tokens=self.max_new_tokens,
            safety_margin_tokens=self.safety_margin_tokens,
            requires_workspace_change=self.requires_workspace_change,
            feature_flags=self.feature_flags,
            hooks=self.hooks,
            observer=self.loop_observer,
            tool_validation=self.tool_executor.context.validation,
            secret_env_names=self.secret_env_names,
            metadata_state=self._loop_metadata,
            resume_state=self.resume_state,
            context_assembler=self.context_assembler,
            context_validator=self.context_validator,
            instruction_resolver=self.instruction_resolver,
            instruction_loader=self.instruction_loader,
            agent_role=self.agent_role,
            repository_id=self.repository_id,
            user_instructions=self.user_instructions,
            repository_instructions=self.repository_instructions,
            agent_instructions=self.agent_instructions,
            tool_instructions=self.tool_instructions,
        )
        # Preserve the public compatibility handle without constructing a
        # second context builder bound to the Runtime.
        self.context_manager = self.context_collaborator.legacy_context_manager
        self.agent_loop = AgentLoop(
            self.loop_observer, self.loop_state_collaborator,
            self.context_collaborator, self.model_invoker, self.tool_executor,
            self.injection_provider, self.cancellation_source, self.loop_history, self.run_store,
            self.loop_status, self.hooks, self.model_client, self.requires_workspace_change,
            self.effective_step_budget, self.emergency_cap, self.runtime_mode,
            self.feature_enabled("prompt_cache"),
            self.tools, self.prefix,
        )
        self.turn_runner = TurnRunner(
            _RuntimeTurnLifecycle(self, self.run_store), self.agent_loop, self.hooks
        )

    @property
    def approval_handler(self):
        return self._approval_handler_ref.get("value")

    @approval_handler.setter
    def approval_handler(self, handler):
        if hasattr(self, "_approval_handler_ref"):
            self._approval_handler_ref["value"] = handler

    @property
    def cancel_checker(self):
        return self._cancel_checker_ref.get("value")

    @cancel_checker.setter
    def cancel_checker(self, checker):
        if hasattr(self, "_cancel_checker_ref"):
            self._cancel_checker_ref["value"] = checker

    @property
    def event_handler(self):
        return self._event_handler_ref.get("value")

    @event_handler.setter
    def event_handler(self, handler):
        if hasattr(self, "_event_handler_ref"):
            self._event_handler_ref["value"] = handler

    def bind_identity(self, identity):
        """Bind the current authenticated identity to the execution gate."""
        self.runtime_identity = identity
        self.capability_policy.bind(identity)

    @property
    def current_task_state(self):
        if hasattr(self, "_tool_state_ref"):
            return self._tool_state_ref.get("current")
        return getattr(self, "_current_task_state", None)

    @current_task_state.setter
    def current_task_state(self, state):
        self._current_task_state = state
        if hasattr(self, "_tool_state_ref"):
            self._tool_state_ref["current"] = state

    @classmethod
    def from_session(cls, model_client, workspace, session_store, session_id, **kwargs):
        return cls(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            session=SessionManager(session_store, workspace.repo_root, memorylib.default_memory_state).load(session_id),
            **kwargs,
        )

    @staticmethod
    def normalize_execution_mode(value):
        mode = str(value or EXECUTION_MODE_SINGLE).strip().replace("-", "_").lower()
        if mode in {"multi", "multiagent"}:
            mode = EXECUTION_MODE_MULTI_AGENT
        if mode not in EXECUTION_MODES:
            raise ValueError(
                "execution_mode must be 'single' or 'multi_agent'"
            )
        return mode

    def multi_agent_enabled(self):
        return self.execution_mode == EXECUTION_MODE_MULTI_AGENT

    def _ensure_session_shape(self):
        return self.session_manager.ensure_shape(self.session)

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
            "execution_mode": self.execution_mode,
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
        return self.session_manager.checkpoint_state(self.session)

    def current_checkpoint(self):
        self._ensure_session_shape()
        return self.session_manager.current_checkpoint(self.session)

    def invalidate_stale_memory(self):
        invalidated = self.memory.invalidate_stale_file_summaries()
        self.session["memory"] = self.memory.to_dict()
        return invalidated

    def evaluate_resume_state(self):
        invalidated = self.invalidate_stale_memory()
        # Phase 3: Evidence freshness 对照 live workspace 重算（stale/missing）。
        if self.memory_v2_enabled():
            try:
                self.memory_v2.refresh_freshness()
            except Exception:
                pass
        return self.session_manager.evaluate_resume(
            self.session,
            invalidated=invalidated,
            file_freshness=lambda path: memorylib.file_freshness(path, self.root),
            runtime_identity=self.current_runtime_identity,
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            statuses={
                "none": CHECKPOINT_NONE_STATUS,
                "schema_mismatch": CHECKPOINT_SCHEMA_MISMATCH_STATUS,
                "partial_stale": CHECKPOINT_PARTIAL_STALE_STATUS,
                "workspace_mismatch": CHECKPOINT_WORKSPACE_MISMATCH_STATUS,
                "full_valid": CHECKPOINT_FULL_VALID_STATUS,
            },
        )

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
        subject = getattr(self, "tool_subject", self)
        tools = toolkit.build_tool_registry(subject)
        if self.allowed_tools is None:
            return tools
        # Keep the same live Registry seam for role-filtered agents.  The
        # registry remains mapping-compatible, so existing prompt and test
        # code does not need a special branch.
        return tools.filtered(self.allowed_tools)

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
            hybrid_context_enabled=self.feature_enabled("hybrid_context_enabled"),
        )

    def _pinned_extra(self, user_message=None):
        """Compatibility source accessor; final composition lives in ContextAssembler."""

        return self.context_collaborator.pinned_extra(user_message)

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
        blockers = "|".join(str(item.get("text", "")) for item in (ws.blockers or []))
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
        """Compatibility source accessor owned by the context collaborator."""

        return self.context_collaborator.memory_layer()

    def record_memory_v2_evidence(self, name, args, result):
        """客观工具事件 → Evidence Store（read/symbol/outline/references/verification）。"""
        if not self.memory_v2_enabled() or not self.feature_enabled("evidence_memory"):
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
            self.current_task_state.run_id
            if self.current_task_state is not None
            else ""
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
        """Compatibility hook delegated to the context collaborator."""

        return self.context_collaborator._add_legacy_metadata_compat(
            metadata, prompt, user_message
        )

    def _build_prompt_and_metadata(
        self, user_message, status_callback=None, task_state=None
    ):
        """Compatibility facade; ContextAssembler owns context construction."""

        result = self.context_collaborator.build(
            user_message,
            status_callback=status_callback,
            task_state=task_state,
        )
        # The deprecated Pico facade still exposes prefix/workspace attributes;
        # mirror the explicit context collaborator's refreshed values without
        # giving the assembler a Runtime callback or reference.
        self._apply_prefix_state(self.context_collaborator.prefix_state)
        self.workspace = self.context_collaborator.workspace
        return result

    def emit_trace(self, task_state, event, payload=None):
        return self.loop_observer.emit(task_state, event, payload)

    def _emit_gateway_event(self, event, payload):
        if self.current_task_state is not None:
            self.emit_trace(self.current_task_state, event, payload)

    @property
    def completion_client(self):
        return self.model_gateway or self.model_client

    @staticmethod
    def _canonical_event_name(event):
        mapping = {
            "run_started": "run.started",
            "run_completed": "run.completed",
            "model_requested": "model.started",
            "tool_executed": "tool.completed",
            "workspace_changed": "workspace.changed",
        }
        return mapping.get(event, str(event).replace("_", "."))

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
            for item in self.working_state.relevant_symbols or []:
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
            # Checkpoint resume remains in the same logical task scope, so the
            # durable side-effect ledger must not be discarded on resume.
            "side_effect_operations": dict(task_state.side_effect_operations or {}),
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
            try:
                content_hash = hashlib.sha256(
                    self.path(path).read_bytes()
                ).hexdigest()
            except OSError:
                content_hash = ""
            key = file_summary_cache_key(
                canonical_path,
                content_hash,
                getattr(self.model_client, "model", "local"),
                "read-summary-v1",
            )
            summary = self.file_summary_cache.get(key)
            if summary is None:
                summary = memorylib.summarize_read_result(result)
                self.file_summary_cache.set(key, summary)
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
        """Public compatibility facade for the Phase 6 TurnRunner boundary."""
        return self.turn_runner.run(user_message, run_id=run_id)

    def _ask_legacy(self, user_message, run_id="", preparation=None):
        """Compatibility entry point; AgentLoop owns the model/tool loop."""
        if preparation is None:
            preparation = self.turn_runner.lifecycle.initialize(user_message, run_id)
        self.turn_runner.lifecycle.bind_loop(self.agent_loop)
        return self.agent_loop.run(user_message, run_id=run_id, preparation=preparation)

    def stop_finalization_failed_run(
        self, task_state, user_message, run_started_wall, run_started_at
    ):
        return AgentLoop.finalization_failed(
            task_state, user_message, run_started_at, run_started_wall,
        )

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
        return AgentLoop.limited(
            task_state, user_message, attempts, attempt_cap, tool_steps,
            self.effective_step_budget, run_started_at, run_started_wall,
        )

    def _advance_watchdog(self, task_state, name, args, result, step, metadata=None):
        """把一次工具事件交给 Progress Watchdog，并发射 trace 事件。

        suspected_now 触发时由 ask() 负责注入 Recovery Turn 提示；本方法只做
        watchdog 推进与可观测性记录。返回 WatchdogDecision。

        `metadata` 缺省使用 `_last_tool_result_metadata`（真实工具执行路径）；
        也可显式传入（例如 EditDecisionWatchdog 拒绝重复 evidence 时合成的
        rejected 事件，此时没有真实工具执行）。
        """
        return self.loop_state_collaborator.observe_watchdog(
            task_state, name, args,
            self._last_tool_result_metadata if metadata is None else metadata,
            result, step,
        )

    def stop_stuck_confirmed_run(
        self,
        task_state,
        user_message,
        run_started_wall,
        run_started_at,
    ):
        """STUCK_CONFIRMED：experiment 以 stop_reason 结束；interactive graceful stop。"""
        return AgentLoop.stuck(
            task_state, user_message, runtime_mode=self.runtime_mode,
            pattern=self.watchdog.current_pattern,
            last_reason=self.watchdog.last_progress_reason,
            last_step=self.watchdog.last_progress_step,
            interactive_mode=RUNTIME_MODE_INTERACTIVE,
            started_at=run_started_at, started_wall=run_started_wall,
        )

    def stop_emergency_cap_run(
        self,
        task_state,
        user_message,
        emergency_cap,
        run_started_wall,
        run_started_at,
    ):
        """Interactive 模式的 emergency fuse：只兜 Runtime Bug / watchdog 漏检 / runaway。"""
        return AgentLoop.emergency_cap(
            task_state, user_message, emergency_cap, run_started_at, run_started_wall,
        )

    @staticmethod
    def _side_effect_args_digest(args):
        normalized = json.dumps(args or {}, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _side_effect_result_metadata(metadata):
        return {
            key: (metadata or {}).get(key)
            for key in (
                "tool_status",
                "tool_error_code",
                "tool_execution_success",
                "tool_business_success",
                "tool_result_validation_failed",
                "tool_result_failure_reason",
                "workspace_changed",
            )
        }

    def run_tool(self, name, args, operation_key=""):
        return self.tool_executor.execute(name, args, operation_key=operation_key)

    def _run_tool_legacy(self, name, args, operation_key=""):
        """Compatibility entry point; ToolExecutor owns the implementation."""
        return self.tool_executor.execute(name, args, operation_key=operation_key)

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
            "read_range_ledger": [],
            "read_guard_triggered": 0,
            "redundant_read_suppressed": 0,
            "redundant_read_forced": 0,
            "read_range_ledger_entries": 0,
            "read_guard_delta_reads": 0,
            "read_guard_delta_lines_returned": 0,
            "read_guard_covered_lines_skipped": 0,
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
        self.loop_state_collaborator.synchronize(planning=self.current_planning)
        profile = getattr(self.model_client, "connection_profile", None)
        requires_two_source_reads = bool(
            getattr(self.model_client, "supports_native_tools", False)
            and profile is not None
            and not getattr(profile, "supports_tool_choice", False)
        )
        return self.loop_state_collaborator.update_planning_state(
            name, args, metadata, tool_step,
            requires_workspace_change=self.requires_workspace_change,
            source_read_count=len(self.current_run_source_reads),
            requires_two_source_reads=requires_two_source_reads,
        )

    def evidence_ledger_entries(self):
        return list(self.current_planning.get("evidence_ledger", []))

    def read_range_ledger_entries(self):
        return list(self.current_planning.get("read_range_ledger", []))

    def _read_range_version(self, path):
        return memorylib.file_freshness(task_policy.canonical_path(path), self.root)

    def assess_read_range(self, args):
        path = task_policy.canonical_path((args or {}).get("path"))
        if not path:
            return None, ""
        freshness = self._read_range_version(path)
        coverage = analyze_read_coverage(
            path,
            int((args or {}).get("start", 1)),
            int((args or {}).get("end", 200)),
            self.current_planning.get("read_range_ledger", []),
            freshness,
        )
        return coverage, freshness

    def record_read_range(self, args, step):
        path = task_policy.canonical_path((args or {}).get("path"))
        if not path:
            return
        record = ReadRangeRecord(
            path=path,
            start_line=int((args or {}).get("start", 1)),
            end_line=int((args or {}).get("end", 200)),
            freshness=self._read_range_version(path),
            step=int(step or 0),
        )
        ledger = self.current_planning.setdefault("read_range_ledger", [])
        previous = [
            item if isinstance(item, ReadRangeRecord) else ReadRangeRecord(**item)
            for item in ledger
        ]
        ledger[:] = [
            {
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "freshness": item.freshness,
                "step": item.step,
            }
            for item in merge_read_ranges(previous, record)[-READ_RANGE_LEDGER_LIMIT:]
        ]
        self.current_planning["read_range_ledger_entries"] = len(ledger)

    def invalidate_read_ranges(self, paths):
        changed = {task_policy.canonical_path(path) for path in (paths or [])}
        if not changed:
            return
        # Keep old-version records as stale history.  Freshness filtering prevents
        # them from suppressing a new read, while preserving the stale_read trace.
        ledger = self.current_planning.get("read_range_ledger", [])
        self.current_planning["read_range_ledger_entries"] = len(
            ledger
        )

    def redundant_read_notice(self, args, coverage, action):
        path = task_policy.canonical_path((args or {}).get("path"))
        start, end = int((args or {}).get("start", 1)), int((args or {}).get("end", 200))
        covered = ", ".join(
            f"L{range_start}-L{range_end}"
            for range_start, range_end in coverage.covered_ranges
        )
        return (
            f"Read guard: {path} L{start}-L{end} is already covered by previous "
            f"read evidence ({covered}) at step {coverage.previous_step}; unchanged=true. "
            f"No source was returned; if raw source is specifically needed, call "
            f"read_file(..., force=True). action={action}."
        )

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
        self.loop_state_collaborator.synchronize(planning=self.current_planning)
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
        self.loop_state_collaborator.record_read_evidence(
            args, result, tool_step, freshness=entry["freshness"], hint=entry["hint"]
        )
        ledger = self.current_planning["evidence_ledger"]
        if len(ledger) > EVIDENCE_LEDGER_LIMIT:
            del ledger[: len(ledger) - EVIDENCE_LEDGER_LIMIT]
            self.current_planning["evidence_eviction_count"] += 1

    def invalidate_evidence_for_paths(self, paths):
        self.loop_state_collaborator.synchronize(planning=self.current_planning)
        return self.loop_state_collaborator.invalidate_evidence_for_paths(paths)

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

    def drain_runtime_injections(self, task_state):
        provider = getattr(self, "injection_provider", None)
        if provider is None:
            return []
        injected = list(provider() or [])
        for item in injected:
            message = str(getattr(item, "message", item)).strip()
            if not message:
                continue
            self.loop_state_collaborator.adopt_protected_constraint(task_state, message)
        return injected

    def user_message_with_runtime_constraints(self, user_message):
        if not self.protected_runtime_constraints:
            return user_message
        constraints = "\n".join(f"- {item}" for item in self.protected_runtime_constraints)
        return f"{user_message}\n\nProtected runtime constraints (must obey):\n{constraints}"

    def cancellation_requested(self, task_state):
        checker = getattr(self, "cancel_checker", None)
        if checker is None:
            return False
        return bool(checker(self, task_state))

    def stop_user_canceled_run(self, task_state, run_started_wall, run_started_at):
        return AgentLoop.cancelled(
            task_state, task_state.user_request, run_started_at, run_started_wall,
        )

    def stop_model_error_run(
        self, task_state, exc, model_started_at, run_started_wall, run_started_at
    ):
        error_message = self.redact_text(str(exc))
        error_type = exc.__class__.__name__
        return AgentLoop.model_error(
            task_state, task_state.user_request, error_type, error_message,
            model_started_at, run_started_at, run_started_wall,
        )

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
            "sandbox": self.sandbox.describe(),
        }

    def tool_example(self, name):
        return toolkit.tool_example(name)

    def validate_tool(self, name, args):
        """把通用工具校验和 runtime 级额外约束串起来。"""
        toolkit.validate_tool(self, name, args)
        if name == "delegate":
            if self.depth >= self.max_depth:
                raise ValueError("delegate depth exceeded")

    async def connect_mcp_servers(self, configs):
        """Explicitly connect MCP servers and register tools on this runtime."""
        from .mcp import McpManager

        if self.mcp_manager is None:
            self.mcp_manager = McpManager()
        return await self.mcp_manager.connect_servers(configs, self.tools)

    async def close_mcp_servers(self):
        if self.mcp_manager is not None:
            await self.mcp_manager.close()
            self.mcp_manager = None

    def mcp_snapshot(self):
        return self.mcp_manager.snapshot() if self.mcp_manager is not None else {"servers": [], "errors": []}

    def activate_extensions(self, names=None, *, granted_capabilities=()):
        """Activate discovered extensions with an explicit capability grant."""
        from .extensions import ExtensionContext

        registry = getattr(self, "extension_registry", None)
        if registry is None:
            raise RuntimeError("extension registry has not been attached")
        context = ExtensionContext(
            runtime=self,
            tool_registry=self.tools,
            event_bus=self.event_bus,
            granted_capabilities=frozenset(
                str(item).strip() for item in (granted_capabilities or ()) if str(item).strip()
            ),
            metadata={"workspace": str(self.root)},
        )
        return registry.activate_all(names, context=context)

    def deactivate_extensions(self):
        registry = getattr(self, "extension_registry", None)
        if registry is not None:
            registry.deactivate_all()

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

    def _recover_native_text_tool_call(self, content):
        """Strictly recover one legacy JSON tool envelope for native providers.

        This is deliberately narrower than the legacy text protocol.  It only
        turns an otherwise unambiguous textual intent into a normal ToolCall;
        approval and execution remain in ``run_tool``.
        """
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
        if not isinstance(payload, dict):
            return None, "schema_failure"
        if set(payload) != {"name", "args"}:
            return None, "schema_failure"
        name = payload.get("name")
        args = payload.get("args")
        if not isinstance(name, str) or not name.strip():
            return None, "schema_failure"
        if not isinstance(args, dict):
            return None, "invalid_args"
        name = name.strip()
        tool = self.tools.get(name)
        if tool is None:
            return None, "unknown_tool"
        if isinstance(tool.get("parameters"), dict):
            try:
                toolkit.validate_json_tool_arguments(tool, args)
            except ValueError:
                return None, "schema_failure"
        else:
            declared = tool.get("schema", {})
            if set(args) - set(declared):
                return None, "schema_failure"
            for key, value in args.items():
                type_name = str(declared[key]).partition("=")[0]
                valid = {
                    "str": isinstance(value, str),
                    "int": isinstance(value, int) and not isinstance(value, bool),
                    "float": isinstance(value, (int, float)) and not isinstance(value, bool),
                    "bool": isinstance(value, bool),
                }.get(type_name, False)
                if not valid:
                    return None, "schema_failure"
        try:
            self.validate_tool(name, args)
        except Exception:
            return None, "invalid_args"
        return ToolCall(f"legacy-recovered-{uuid.uuid4().hex}", name, args), None

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
        # 所有文件类工具都被锚定在 workspace root 之下。
        # 这样既能防住 "../" 逃逸，也能防住符号链接解析后跳出仓库。
        return self.sandbox.resolve_path(raw_path)


MiniAgent = Pico
