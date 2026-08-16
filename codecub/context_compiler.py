"""Context Compiler — 决定“下一次 Model Request 应看到什么”。

Phase 2 目标：用

    Pinned Context + Working State + Recent Verbatim
    + Compressed History + Symbol Repo Map

取代“删除 / truncate / oldest-first pruning”的旧 Context 策略。

职责边界：
- 本模块只负责“模型下一轮看到的 Context”；
- 不负责长期 Memory（Phase 3）、Runtime Stuck 判断（Watchdog）、
  Tool Safety、Verifier；
- 不删除任何 Raw Runtime Artifact（trace / tool_call / tool_result /
  assistant message / test output / patch / usage 仍完整保存）。

两条管线：
- `compile_text(...)`：legacy text 模式（session["history"] → 文本 prompt）。
- `compile_native(...)`：native tool 模式（native_messages → 压缩后的
  messages 列表，保持 assistant.tool_calls + tool result 原子性）。

压缩原则：
- Stage A — Deduplicate：去重 pinned / 静态元数据 / 完全重复 item；
- Stage B — Compact bulk：非常旧的巨型 tool output 换成
  Tool Result Summary + event reference + key facts；
- Stage C — Structured History Condensation：更老 history 才做结构化总结；
- Stage D — Recursive Condensation：旧 summary 可再次压缩，但必须保留
  user goal / unresolved blocker / changed files / decisions /
  verification status / provenance。
禁止 blind pruning（while tokens > budget: history.pop(0)）。

Freshness：fact 记录 source_hash（复用 memory.file_freshness）；文件修改后
旧 fact 标记 stale，不允许把 stale fact 当 current truth 展示。

Working State 生命周期：Task Start → Task-local → Task Finish → archive/discard。
它不自动写入 .codecub/memory（那是 Phase 3 Durable Memory 的职责）。
"""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from .task_policy import canonical_path, normalize_shell_command

# ---------------------------------------------------------------------------
# 集中配置（阈值 / 预算比例统一在这里，避免 magic number 散落）
# ---------------------------------------------------------------------------

# 可用的输入预算来自 model_context_window - reserved_output - tool_overhead -
# safety_margin；window 未知时用保守 fallback。
DEFAULT_USABLE_BUDGET_FALLBACK = 12000
DEFAULT_RESERVED_OUTPUT_TOKENS = 1024
DEFAULT_SAFETY_MARGIN_TOKENS = 256
DEFAULT_TOOL_SCHEMA_OVERHEAD_TOKENS = 800

# 压缩触发阈值：estimated_candidate_input / usable_input_budget 达到该值才触发。
DEFAULT_COMPRESSION_TRIGGER_THRESHOLD = 0.75

# 各层在 usable budget 中的默认占比（compiled 各段再按实际内容收缩）。
DEFAULT_LAYER_BUDGET_RATIOS = {
    "pinned": 0.12,
    "working_state": 0.15,
    "recent_verbatim": 0.38,
    "compressed_history": 0.20,
    "repo_map": 0.10,
}
DEFAULT_RECENT_VERBATIM_FLOOR_GROUPS = 2  # 至少保留最近 2 个完整 native group

# Working State 上界（bounded）。
WORKING_STATE_MAX_FACTS = 24
WORKING_STATE_MAX_CHANGED_FILES = 12
WORKING_STATE_MAX_VERIFICATIONS = 8
WORKING_STATE_MAX_BLOCKERS = 6
WORKING_STATE_MAX_SYMBOLS = 16
WORKING_STATE_MAX_FAILED_APPROACHES = 6
WORKING_STATE_MAX_PENDING_QUESTIONS = 4

# Compressed History 中保留的条目上限（Recursive Condensation 时 summary 自身也受限）。
MAX_COMPRESSED_HISTORY_ENTRIES = 40

# Provenance 字段。
PROVENANCE_FIELDS = (
    "path",
    "symbol",
    "line_range",
    "event_id",
    "tool_call_id",
    "source_hash",
    "step",
    "timestamp",
)

# Kind 常量。
ITEM_KIND_PINNED = "pinned"
ITEM_KIND_WORKING_STATE = "working_state"
ITEM_KIND_RECENT_VERBATIM = "recent_verbatim"
ITEM_KIND_COMPRESSED_HISTORY = "compressed_history"
ITEM_KIND_REPO_MAP = "repo_map"

# 稳定 ID 前缀。
PINNED_USER_TASK = "pinned:user-task"
PINNED_PROJECT_RULES = "pinned:project-rules"
PINNED_SAFETY = "pinned:safety"
PINNED_WORKSPACE = "pinned:workspace"
PINNED_RUNTIME_MODE = "pinned:runtime-mode"
PINNED_TOOL_CONTRACT = "pinned:tool-contract"


@dataclass
class ContextItem:
    """一次可审计的 Context 单元。

    key 用于 dedup（稳定 ID）；kind 标识属于哪一层；text 是渲染进 model 的
    文本；provenance 回答“这个事实从哪里来”；freshness_hash 用于 stale 判定。
    """

    key: str
    kind: str
    text: str
    provenance: dict = field(default_factory=dict)
    freshness_hash: str = ""

    def token_count(self, counter):
        return counter.count(self.text) if counter is not None else len(self.text)


@dataclass
class WorkingState:
    """Task-local 工作状态：只描述当前任务，不跨 task/session 保存。

    由客观 Runtime Event 驱动更新（workspace change、test 结果、patch 拒绝、
    read/symbol 等），不允许 LLM 每轮自由“写作文”。所有字段 bounded。
    """

    goal: str = ""
    known_facts: list = field(default_factory=list)  # {text, provenance, source_hash}
    changed_files: list = field(default_factory=list)  # [path]
    verification: list = field(default_factory=list)  # [{command, status, error_sig, step}]
    blockers: list = field(default_factory=list)  # [{text, step}]
    next_step: str = ""
    relevant_symbols: list = field(default_factory=list)  # [{name, path, kind}]
    failed_approaches: list = field(default_factory=list)  # [{text, step}]
    pending_questions: list = field(default_factory=list)  # [str]
    last_updated_step: int = 0

    # ------------------------------------------------------------------
    # 客观事件更新
    # ------------------------------------------------------------------

    def update_from_tool_event(self, name, args, metadata, result_text, step, workspace_root=None):
        """根据一次工具执行结果更新 Working State。"""
        self.last_updated_step = max(self.last_updated_step, int(step or 0))
        metadata = metadata or {}
        if bool(metadata.get("workspace_changed")):
            for path in metadata.get("affected_paths") or []:
                self.add_changed_file(str(path))
            # 代码变化后，同路径旧 fact 失效。
            changed = {canonical_path(str(p)) for p in (metadata.get("affected_paths") or [])}
            if changed:
                self.invalidate_facts_for_paths(changed)
        status = str(metadata.get("tool_status", ""))
        if name == "run_shell":
            command = normalize_shell_command(args)
            error_sig = self._error_signature(metadata, result_text)
            self._record_verification(command, status, error_sig, step)
            if error_sig:
                self.add_failed_approach(
                    f"verification failed: {command[:80]} ({error_sig})", step
                )
        if name in {"patch_file", "write_file"} and status == "rejected":
            self.add_failed_approach(f"{name} rejected on {args.get('path', '?')}", step)
        if name == "read_file" and status != "rejected":
            path = str(args.get("path", "")).strip()
            if path:
                self.add_relevant_symbol(path=path, name="", kind="file")
        if name == "symbol_search" and status != "rejected":
            query = str(args.get("query", "")).strip()
            if query:
                self.add_relevant_symbol(path=str(args.get("path", ".")), name=query, kind="symbol_query")
        return self

    @staticmethod
    def _error_signature(metadata, result_text):
        status = str((metadata or {}).get("tool_status", "")).strip()
        code = str((metadata or {}).get("tool_error_code", "")).strip()
        if status not in {"error", "rejected"} and not code:
            return ""
        match = re.search(r"exit_code:\s*(-?\d+)", str(result_text or ""))
        exit_code = match.group(1) if match else ""
        return f"{status}|{code}|{exit_code}"

    def _record_verification(self, command, status, error_sig, step):
        entry = {
            "command": command[:120],
            "status": "ok" if status in {"ok", "partial_success"} else status,
            "error_sig": error_sig,
            "step": int(step or 0),
        }
        # 同命令的旧验证条目被新结果取代（只保留当前状态 + 少量关键历史）。
        self.verification = [
            item for item in self.verification if item.get("command") != entry["command"]
        ]
        self.verification.append(entry)
        del self.verification[:-WORKING_STATE_MAX_VERIFICATIONS]

    # ------------------------------------------------------------------
    # 有界更新 helper
    # ------------------------------------------------------------------

    def set_goal(self, goal):
        self.goal = str(goal or "").strip()

    def set_next_step(self, next_step):
        self.next_step = str(next_step or "").strip()

    def add_blocker(self, text, step=0):
        text = str(text or "").strip()
        if not text:
            return
        self.blockers = [b for b in self.blockers if b.get("text") != text]
        self.blockers.append({"text": text[:300], "step": int(step or 0)})
        del self.blockers[:-WORKING_STATE_MAX_BLOCKERS]

    def clear_blocker(self, text=None):
        if text is None:
            self.blockers = []
            return
        self.blockers = [b for b in self.blockers if b.get("text") != text]

    def add_changed_file(self, path):
        canonical = canonical_path(str(path))
        if not canonical:
            return
        self.changed_files = [p for p in self.changed_files if canonical_path(p) != canonical]
        self.changed_files.append(canonical)
        del self.changed_files[:-WORKING_STATE_MAX_CHANGED_FILES]

    def add_known_fact(self, text, provenance=None, source_hash=""):
        text = str(text or "").strip()
        if not text or REDACTED_VALUE in text:
            return
        provenance = dict(provenance or {})
        self.known_facts = [
            fact
            for fact in self.known_facts
            if str(fact.get("text", "")).strip() != text
        ]
        self.known_facts.append(
            {"text": text[:400], "provenance": provenance, "source_hash": str(source_hash or "")}
        )
        del self.known_facts[:-WORKING_STATE_MAX_FACTS]

    def add_relevant_symbol(self, path="", name="", kind="symbol"):
        canonical = canonical_path(str(path))
        name = str(name or "").strip()
        if not canonical and not name:
            return
        self.relevant_symbols = [
            item
            for item in self.relevant_symbols
            if not (item.get("path") == canonical and item.get("name") == name)
        ]
        self.relevant_symbols.append({"path": canonical, "name": name, "kind": str(kind or "symbol")})
        del self.relevant_symbols[:-WORKING_STATE_MAX_SYMBOLS]

    def add_failed_approach(self, text, step=0):
        text = str(text or "").strip()
        if not text:
            return
        self.failed_approaches = [
            item
            for item in self.failed_approaches
            if str(item.get("text", "")).strip() != text
        ]
        self.failed_approaches.append({"text": text[:300], "step": int(step or 0)})
        del self.failed_approaches[:-WORKING_STATE_MAX_FAILED_APPROACHES]

    def add_pending_question(self, question):
        question = str(question or "").strip()
        if not question:
            return
        self.pending_questions = [q for q in self.pending_questions if q != question]
        self.pending_questions.append(question[:200])
        del self.pending_questions[:-WORKING_STATE_MAX_PENDING_QUESTIONS]

    def invalidate_facts_for_paths(self, changed_paths):
        """文件修改后，来源指向这些路径的 fact 标记 stale。"""
        changed = {canonical_path(str(p)) for p in (changed_paths or [])}
        if not changed:
            return
        for fact in self.known_facts:
            provenance = fact.get("provenance") or {}
            path = canonical_path(str(provenance.get("path", "")))
            if path and path in changed:
                fact["stale"] = True

    # ------------------------------------------------------------------
    # Freshness
    # ------------------------------------------------------------------

    def refresh_fact_freshness(self, workspace_root):
        """对照当前文件 hash，把过期的 fact 标记 stale。"""
        from .memory import file_freshness

        for fact in self.known_facts:
            provenance = fact.get("provenance") or {}
            path = str(provenance.get("path", "")).strip()
            source_hash = str(fact.get("source_hash", "") or "")
            if not path or not source_hash:
                continue
            current = file_freshness(path, workspace_root)
            if current and current != source_hash:
                fact["stale"] = True

    def fresh_facts(self):
        return [fact for fact in self.known_facts if not fact.get("stale")]

    def stale_facts(self):
        return [fact for fact in self.known_facts if fact.get("stale")]

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def to_text(self):
        lines = ["Working State:"]
        if self.goal:
            lines.append(f"- Goal: {self.goal[:300]}")
        fresh = self.fresh_facts()
        stale = self.stale_facts()
        if fresh:
            lines.append("- Known Facts:")
            for fact in fresh:
                text = str(fact.get("text", ""))[:200]
                provenance = fact.get("provenance") or {}
                ref = provenance.get("path", "")
                if ref:
                    text = f"{text} (source: {ref})"
                lines.append(f"  * {text}")
        if stale:
            lines.append("- Previously observed, now stale:")
            for fact in stale:
                lines.append(f"  * {str(fact.get('text', ''))[:160]}")
        if self.changed_files:
            lines.append("- Changed Files: " + ", ".join(self.changed_files[:WORKING_STATE_MAX_CHANGED_FILES]))
        if self.verification:
            latest = self.verification[-1]
            lines.append(
                f"- Verification: {latest.get('command')} -> {latest.get('status')}"
            )
            failures = [v for v in self.verification if v.get("status") != "ok"]
            if len(failures) > 1:
                lines.append(f"  (recent failures: {len(failures)})")
        if self.blockers:
            lines.append("- Blockers:")
            for blocker in self.blockers:
                lines.append(f"  * {blocker.get('text', '')}")
        if self.next_step:
            lines.append(f"- Next Step: {self.next_step[:300]}")
        if self.relevant_symbols:
            symbols = sorted(
                {
                    f"{item.get('path', '')}:{item.get('name', '')}"
                    for item in self.relevant_symbols
                    if item.get('path') or item.get('name')
                }
            )[:WORKING_STATE_MAX_SYMBOLS]
            lines.append("- Relevant Symbols: " + ", ".join(symbols))
        if self.failed_approaches:
            lines.append("- Failed Approaches:")
            for approach in self.failed_approaches:
                lines.append(f"  * {approach.get('text', '')}")
        if self.pending_questions:
            lines.append("- Open Questions:")
            for question in self.pending_questions:
                lines.append(f"  * {question}")
        return "\n".join(lines)

    def to_dict(self):
        return {
            "goal": self.goal,
            "known_facts": list(self.known_facts),
            "changed_files": list(self.changed_files),
            "verification": list(self.verification),
            "blockers": list(self.blockers),
            "next_step": self.next_step,
            "relevant_symbols": list(self.relevant_symbols),
            "failed_approaches": list(self.failed_approaches),
            "pending_questions": list(self.pending_questions),
            "last_updated_step": self.last_updated_step,
        }

    @classmethod
    def from_dict(cls, data):
        state = cls()
        state.goal = str((data or {}).get("goal", ""))
        state.known_facts = list((data or {}).get("known_facts", []))
        state.changed_files = list((data or {}).get("changed_files", []))
        state.verification = list((data or {}).get("verification", []))
        state.blockers = list((data or {}).get("blockers", []))
        state.next_step = str((data or {}).get("next_step", ""))
        state.relevant_symbols = list((data or {}).get("relevant_symbols", []))
        state.failed_approaches = list((data or {}).get("failed_approaches", []))
        state.pending_questions = list((data or {}).get("pending_questions", []))
        state.last_updated_step = int((data or {}).get("last_updated_step", 0))
        return state


@dataclass
class ContextBudget:
    """usable input budget 与压缩触发阈值。

    usable_input_budget = model_context_window - reserved_output_tokens
                          - tool_schema_overhead - safety_margin_tokens
    window 未知时使用 conservative fallback，并记录 budget_source。
    """

    usable_input_budget: int
    trigger_threshold: float = DEFAULT_COMPRESSION_TRIGGER_THRESHOLD
    budget_source: str = "configured"  # configured | fallback
    context_window: Optional[int] = None
    reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS
    tool_schema_overhead: int = DEFAULT_TOOL_SCHEMA_OVERHEAD_TOKENS
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS

    @classmethod
    def resolve(
        cls,
        context_window=None,
        max_new_tokens=None,
        safety_margin_tokens=None,
        tool_schema_overhead=None,
        fallback=DEFAULT_USABLE_BUDGET_FALLBACK,
    ):
        reserved = (
            int(max_new_tokens) if max_new_tokens is not None else DEFAULT_RESERVED_OUTPUT_TOKENS
        )
        safety = (
            int(safety_margin_tokens)
            if safety_margin_tokens is not None
            else DEFAULT_SAFETY_MARGIN_TOKENS
        )
        overhead = (
            int(tool_schema_overhead)
            if tool_schema_overhead is not None
            else DEFAULT_TOOL_SCHEMA_OVERHEAD_TOKENS
        )
        if context_window:
            usable = max(1, int(context_window) - reserved - overhead - safety)
            return cls(
                usable_input_budget=usable,
                context_window=int(context_window),
                reserved_output_tokens=reserved,
                tool_schema_overhead=overhead,
                safety_margin_tokens=safety,
                budget_source="configured",
            )
        return cls(
            usable_input_budget=max(1, int(fallback) - reserved - overhead - safety),
            budget_source="fallback",
        )

    def utilization(self, estimated_tokens):
        if not self.usable_input_budget:
            return 1.0
        return estimated_tokens / self.usable_input_budget

    def should_compress(self, estimated_tokens):
        return self.utilization(estimated_tokens) >= self.trigger_threshold


REDACTED_VALUE = "<redacted>"
SECRET_SHAPED_TEXT_PATTERN = re.compile(
    r"(?i)(\b(api[_ -]?key|token|secret|password)\b|sk-[A-Za-z0-9_-]{6,})"
)


def make_provenance(path="", symbol="", line_range="", event_id="", tool_call_id="", source_hash="", step=0, timestamp=""):
    """构造 provenance dict（缺失字段自动省略）。"""
    return {
        key: value
        for key, value in {
            "path": str(path or ""),
            "symbol": str(symbol or ""),
            "line_range": str(line_range or ""),
            "event_id": str(event_id or ""),
            "tool_call_id": str(tool_call_id or ""),
            "source_hash": str(source_hash or ""),
            "step": int(step or 0),
            "timestamp": str(timestamp or ""),
        }.items()
        if value not in ("", 0)
    }


def compute_source_hash(path, workspace_root=None):
    """复用 memory.file_freshness 作为 source hash（不建 parallel freshness system）。"""
    try:
        from .memory import file_freshness

        return file_freshness(path, workspace_root)
    except Exception:
        return ""


def redact_secret_shaped(text):
    """把 secret-shaped 文本替换为占位符（与 runtime redact 互补）。"""
    text = str(text)
    return SECRET_SHAPED_TEXT_PATTERN.sub(REDACTED_VALUE, text)


class RepoMapSelector:
    """从 CodeIndex 中选择 task-relevant 的 symbol 级 Repo Map。

    只提供结构导航，不代替真实 source read。按：
    - task lexical relevance（user task / working state 里的词）
    - touched files（Working State.changed_files / relevant_symbols）
    - symbol/ref relevance（CodeIndex find_references）
    排序，并受独立 budget 约束。
    """

    def __init__(self, code_index, max_files=10, max_symbols_per_file=12):
        self.code_index = code_index
        self.max_files = int(max_files)
        self.max_symbols_per_file = int(max_symbols_per_file)

    def select(self, task_text, working_state, budget_tokens, counter=None):
        if self.code_index is None:
            return [], {}
        task_text = str(task_text or "")
        touched = {
            canonical_path(str(p))
            for p in (working_state.changed_files if working_state else [])
        }
        touched.update(
            canonical_path(str(item.get("path", "")))
            for item in (working_state.relevant_symbols if working_state else [])
            if item.get("path")
        )
        tokens = _tokenize(task_text)
        candidates = []
        for relative, record in self.code_index.files.items():
            symbols = record.get("symbols", [])
            if not symbols:
                continue
            score = 0
            if canonical_path(relative) in touched:
                score += 100
            names = " ".join(str(item.get("name", "")) for item in symbols)
            names_lower = names.lower()
            score += sum(1 for token in tokens if token in names_lower)
            imports = record.get("imports", [])
            imports_text = " ".join(
                f"{item.get('name', '')} {item.get('module', '')}" for item in imports
            ).lower()
            score += sum(1 for token in tokens if token in imports_text)
            if score > 0:
                candidates.append((score, relative, symbols))
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = []
        used = 0
        for score, relative, symbols in candidates[: self.max_files]:
            lines = [f"{relative}"]
            for symbol in sorted(symbols, key=lambda item: int(item.get("start_line", 0)))[
                : self.max_symbols_per_file
            ]:
                lines.append(
                    f"  {symbol.get('kind', 'symbol')} {symbol.get('qualified_name', symbol.get('name', ''))} @ L{int(symbol.get('start_line', 0))}"
                )
            block = "\n".join(lines)
            block_tokens = counter.count(block) if counter is not None else len(block)
            if used + block_tokens > budget_tokens and used > 0:
                break
            selected.append(block)
            used += block_tokens
        details = {
            "candidates": len(candidates),
            "selected_files": len(selected),
            "estimated_tokens": used,
        }
        return selected, details


def _tokenize(text):
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", str(text))}


class HistoryCondenser:
    """对旧 History 做结构化总结。

    两种模式：
    - deterministic：不调用 LLM，从 history 事件客观提取（decisions / findings /
      files / symbols / changes / verification / failed approaches / blockers）。
    - llm：调用模型生成结构化 summary；失败时回退 deterministic，绝不以
      summary="" 删除原 history。

    与主 Agent 推理完全隔离（独立 client / timeout / error handling /
    token accounting / secret redaction / structured validation）。
    """

    def __init__(
        self,
        model_client=None,
        timeout_seconds=30,
        max_input_chars=12000,
        token_counter=None,
        redact_fn=None,
    ):
        self.model_client = model_client
        self.timeout_seconds = int(timeout_seconds)
        self.max_input_chars = int(max_input_chars)
        self.token_counter = token_counter
        self.redact_fn = redact_fn or (lambda text: text)
        self.last_error = ""

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def condense(self, history, goal="", step=0):
        """把旧 history 压缩为结构化 summary。失败时回退 deterministic 且保留 raw。

        返回 (summary_text, meta)：
        - summary_text 是压缩后的结构化文本；
        - meta 记录 mode（llm/deterministic/fallback）、错误、token 估算。
        """
        raw_count = len(history)
        deterministic = self._deterministic_condense(history, goal, step)
        if self.model_client is None:
            meta = {
                "mode": "deterministic",
                "raw_entries": raw_count,
                "error": "",
            }
            return deterministic, meta
        try:
            llm_summary = self._llm_condense(history, goal, step)
            meta = {
                "mode": "llm",
                "raw_entries": raw_count,
                "error": "",
            }
            return llm_summary, meta
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            meta = {
                "mode": "deterministic_fallback",
                "raw_entries": raw_count,
                "error": self.last_error,
            }
            return deterministic, meta

    # ------------------------------------------------------------------
    # Deterministic 模式
    # ------------------------------------------------------------------

    def _deterministic_condense(self, history, goal, step):
        findings, changed, verification, failed, decisions, symbols = [], [], [], [], [], []
        seen_files = set()
        redact = self.redact_fn or (lambda text: text)
        for index, item in enumerate(history):
            role = str(item.get("role", ""))
            name = str(item.get("name", ""))
            args = item.get("args") or {}
            content = str(item.get("content", ""))
            if role == "tool" and name == "read_file":
                path = str(args.get("path", "")).strip()
                if path and path not in seen_files:
                    seen_files.add(path)
                    findings.append(f"investigated {redact(path)}")
            elif role == "tool" and name == "search":
                findings.append(f"searched {redact(str(args.get('pattern', ''))[:60])}")
            elif role == "tool" and name == "symbol_search":
                symbols.append(redact(str(args.get("query", ""))[:60]))
            elif role == "tool" and name == "run_shell":
                command = redact(str(args.get("command", ""))[:80])
                ok = "exit_code: 0" in content or "error" not in content.lower()
                verification.append(f"{command} -> {'pass' if ok else 'fail'}")
                if not ok:
                    failed.append(f"{command} failed")
            elif role == "tool" and name in {"patch_file", "write_file"}:
                changed.append(redact(str(args.get("path", ""))[:80]))
                if "error" in content.lower():
                    failed.append(f"{name} on {redact(str(args.get('path', ''))[:60])} rejected")
        lines = ["Earlier Investigation:"]
        if goal:
            lines.append(f"- Goal: {goal[:200]}")
        if decisions:
            lines.append("- Decisions:")
            for decision in decisions[-8:]:
                lines.append(f"  * {decision}")
        if findings:
            lines.append("- Findings:")
            for finding in findings[-12:]:
                lines.append(f"  * {finding}")
        if symbols:
            lines.append("- Symbols:")
            for symbol in symbols[-8:]:
                lines.append(f"  * {symbol}")
        if changed:
            lines.append("- Changed:")
            for change in changed[-8:]:
                lines.append(f"  * {change}")
        if verification:
            lines.append("- Verification:")
            for verify in verification[-8:]:
                lines.append(f"  * {verify}")
        if failed:
            lines.append("- Failed Approaches:")
            for failure in failed[-8:]:
                lines.append(f"  * {failure}")
        if not (findings or changed or verification or failed):
            lines.append("- (no structured signals captured)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM 模式（与主 Agent 隔离）
    # ------------------------------------------------------------------

    _CONDENSE_PROMPT = (
        "Condense the following agent transcript into a structured, concise "
        "summary for a long coding task. Do not invent facts, do not add changes "
        "that did not happen, do not hide failures, and never present stale facts "
        "as current. Preserve decisions, key findings, relevant files/symbols, "
        "changes, verification outcomes, failed approaches, unresolved blockers, "
        "and provenance references.\n\n"
        "Format the answer as plain structured text with sections:\n"
        "Goal-Relevant Findings\nConfirmed Facts\nRelevant Files\nRelevant Symbols\n"
        "Changes\nVerification\nFailed Approaches\nUnresolved Blockers\nProvenance\n"
    )

    def _llm_condense(self, history, goal, step):
        del step
        transcript = self._render_transcript(history)
        if len(transcript) > self.max_input_chars:
            transcript = transcript[: self.max_input_chars] + "\n...(truncated)"
        prompt = self._CONDENSE_PROMPT + "\nGoal: " + str(goal or "")[:200] + "\n\nTranscript:\n" + transcript
        client = self.model_client
        started = time.monotonic()
        try:
            if hasattr(client, "complete"):
                raw = client.complete(prompt, 800)
            else:
                raise RuntimeError("condenser model client has no complete()")
        finally:
            self._last_duration_ms = int((time.monotonic() - started) * 1000)
        text = str(raw or "").strip()
        if not text:
            raise RuntimeError("condenser returned empty summary")
        return self.redact_fn(text)

    def _render_transcript(self, history):
        lines = []
        for item in history:
            role = str(item.get("role", ""))
            name = str(item.get("name", ""))
            if role == "tool":
                lines.append(f"[tool:{name}] {json.dumps(item.get('args', {}), sort_keys=True, ensure_ascii=True)}")
                lines.append(self.redact_fn(str(item.get("content", ""))[:600]))
            else:
                lines.append(f"[{role}] {self.redact_fn(str(item.get('content', '')))[:400]}")
        return "\n".join(lines)

    @property
    def last_duration_ms(self):
        return getattr(self, "_last_duration_ms", 0)


# ---------------------------------------------------------------------------
# Context Compiler 主类
# ---------------------------------------------------------------------------


class ContextCompiler:
    """编译“下一次 Model Request 应看到什么”。

    输入：
    - user_message：当前用户任务（Pinned）。
    - working_state：Task-local Working State。
    - history / native_messages：完整 raw 历史（不删除，只影响 model 可见部分）。
    - pinned_extra：项目规则 / safety / workspace 等稳定上下文（可来自 runtime prefix）。
    - code_index / budget / condenser。

    输出：
    - legacy：compile_text -> (prompt_text, metadata)
    - native：compile_native -> (messages, metadata)
    - compiled：CompiledContext（含各层 token 估算与 provenance）
    """

    def __init__(
        self,
        token_counter=None,
        budget=None,
        condenser=None,
        code_index=None,
        repo_map_selector=None,
        redact_fn=None,
        layer_ratios=None,
        recent_floor_groups=DEFAULT_RECENT_VERBATIM_FLOOR_GROUPS,
        workspace_root=None,
    ):
        self.token_counter = token_counter
        self.budget = budget or ContextBudget.resolve()
        self.condenser = condenser or HistoryCondenser(redact_fn=redact_fn)
        self.code_index = code_index
        self.repo_map_selector = repo_map_selector or RepoMapSelector(code_index)
        self.redact_fn = redact_fn or (lambda text: text)
        self.layer_ratios = dict(DEFAULT_LAYER_BUDGET_RATIOS)
        if layer_ratios:
            self.layer_ratios.update({str(k): float(v) for k, v in layer_ratios.items()})
        self.recent_floor_groups = int(recent_floor_groups)
        self.workspace_root = workspace_root
        # 状态（每轮 compile 更新，供 observability）。
        self.last_compile_metadata = {}
        self.compression_count = 0
        self.compression_failure_count = 0
        self.compressed_summaries = []  # recursive condensation 栈

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def compile_text(self, user_message, working_state=None, history=None, pinned_extra=None):
        """legacy text 模式：从 session history 编译文本 prompt。"""
        working_state = working_state or WorkingState()
        history = list(history or [])
        pinned_extra = pinned_extra or {}
        pinned = self._build_pinned(user_message, pinned_extra)
        estimated = self._estimate_candidate_tokens(pinned, working_state, history)
        should_compress = self.budget.should_compress(estimated)
        compressed_history = []
        recent_items = []
        if should_compress:
            self.compression_count += 1
            compressed_history, recent_items = self._partition_history(
                history, working_state
            )
        else:
            recent_items = self._history_items(history)
        repo_map_items, repo_map_details = self._build_repo_map(user_message, working_state)
        compiled = self._assemble(
            pinned=pinned,
            working_state=working_state,
            recent_items=recent_items,
            compressed_history_items=compressed_history,
            repo_map_items=repo_map_items,
        )
        self.last_compile_metadata = self._metadata(
            user_message=user_message,
            working_state=working_state,
            history=history,
            pinned=pinned,
            recent_items=recent_items,
            compressed_history_items=compressed_history,
            repo_map_items=repo_map_items,
            repo_map_details=repo_map_details,
            should_compress=should_compress,
            estimated_tokens=estimated,
            compiled=compiled,
        )
        return compiled.text, self.last_compile_metadata

    def compile_native(self, user_message, working_state=None, native_messages=None, pinned_extra=None):
        """native 模式：压缩 native_messages，保持 assistant.tool_calls + tool result 原子性。

        返回 (messages, metadata)；messages 始终以合法 native 顺序：
        system / user / assistant(tool_calls) 后紧跟对应 tool 结果。
        """
        working_state = working_state or WorkingState()
        original_messages = native_messages
        native_messages = list(native_messages or [])
        pinned_extra = pinned_extra or {}
        pinned = self._build_pinned(user_message, pinned_extra)
        estimated = self._estimate_native_tokens(native_messages)
        should_compress = self.budget.should_compress(estimated)
        if not should_compress:
            self.last_compile_metadata = self._metadata(
                user_message=user_message,
                working_state=working_state,
                history=native_messages,
                pinned=pinned,
                recent_items=self._native_items(native_messages),
                compressed_history_items=[],
                repo_map_items=[],
                repo_map_details={},
                should_compress=False,
                estimated_tokens=estimated,
                compiled=None,
                native_mode=True,
            )
            # 未压缩时返回原列表引用，保持 runtime 的追加语义不变。
            return (
                original_messages if original_messages is not None else native_messages,
                self.last_compile_metadata,
            )
        self.compression_count += 1
        groups = self._group_native_messages(native_messages)
        recent_groups, older_groups = self._partition_native_groups(groups)
        compressed_history_items, older_raw = self._compress_older_groups(older_groups, working_state)
        repo_map_items, repo_map_details = self._build_repo_map(user_message, working_state)
        # 组装消息：system(pinned + working state + compressed) + recent groups
        messages = []
        pinned_text = self._render_pinned(pinned)
        working_text = working_state.to_text()
        summary_text = self._render_compressed_items(compressed_history_items)
        preamble_parts = []
        if pinned_text:
            preamble_parts.append(pinned_text)
        if working_text.strip():
            preamble_parts.append(working_text)
        if summary_text.strip():
            preamble_parts.append(summary_text)
        if repo_map_items:
            preamble_parts.append("Repository map:\n" + "\n".join(repo_map_items))
        if preamble_parts:
            messages.append(
                {
                    "role": "system",
                    "content": "\n\n".join(preamble_parts),
                }
            )
        for group in recent_groups:
            messages.append(group["message"])
            messages.extend(group.get("results", []))
        self.last_native_messages = messages
        self.last_compile_metadata = self._metadata(
            user_message=user_message,
            working_state=working_state,
            history=native_messages,
            pinned=pinned,
            recent_items=self._native_items(recent_groups),
            compressed_history_items=compressed_history_items,
            repo_map_items=repo_map_items,
            repo_map_details=repo_map_details,
            should_compress=True,
            estimated_tokens=estimated,
            compiled=None,
            native_mode=True,
            older_raw_entries=len(older_raw),
        )
        return messages, self.last_compile_metadata

    # ------------------------------------------------------------------
    # Pinned Context
    # ------------------------------------------------------------------

    def _build_pinned(self, user_message, pinned_extra):
        items = OrderedDict()
        items[PINNED_USER_TASK] = ContextItem(
            key=PINNED_USER_TASK,
            kind=ITEM_KIND_PINNED,
            text=f"User task: {str(user_message).strip()}",
            provenance={"step": 0},
        )
        for key, value in (pinned_extra or {}).items():
            text = str(value or "").strip()
            if not text:
                continue
            # 固定 key 保持稳定 ID；任意额外 key 也作为 pinned 保留
            # （例如 Evidence Ledger），保证“无论压缩多少次都不丢”。
            items[str(key)] = ContextItem(
                key=str(key),
                kind=ITEM_KIND_PINNED,
                text=text,
            )
        return list(items.values())

    @staticmethod
    def _render_pinned(pinned):
        return "\n".join(item.text for item in pinned)

    # ------------------------------------------------------------------
    # Recent Verbatim / 历史分区
    # ------------------------------------------------------------------

    def _history_items(self, history):
        items = []
        for index, item in enumerate(history):
            items.append(
                ContextItem(
                    key=f"history:{index}",
                    kind=ITEM_KIND_RECENT_VERBATIM,
                    text=self._render_history_item(item),
                )
            )
        return items

    def _render_history_item(self, item):
        role = str(item.get("role", ""))
        name = str(item.get("name", ""))
        if role == "tool":
            prefix = f"[tool:{name}] {json.dumps(item.get('args', {}), sort_keys=True, ensure_ascii=True)}"
            content = self.redact_fn(str(item.get("content", "")))
            return f"{prefix}\n{content}"
        content = self.redact_fn(str(item.get("content", "")))
        return f"[{role}] {content}"

    def _native_items(self, groups):
        items = []
        for group in groups:
            message = group.get("message", {})
            text = str(message.get("content") or message.get("tool_calls") or "")
            items.append(
                ContextItem(
                    key=f"native:{group.get('index', 0)}",
                    kind=ITEM_KIND_RECENT_VERBATIM,
                    text=str(text)[:400],
                )
            )
        return items

    def _group_native_messages(self, messages):
        """把 native messages 分成原子组：assistant(tool_calls) + 其 tool results。

        tool 消息的 tool_call_id 必须在同一组 assistant 的 call_ids 中；否则
        视为 orphan 并保留原样（禁止删除）。
        """
        groups = []
        current = None
        for index, message in enumerate(messages):
            role = str(message.get("role", ""))
            if role == "assistant" and message.get("tool_calls"):
                current = {"index": index, "message": message, "results": [], "call_ids": set()}
                for call in message.get("tool_calls") or []:
                    call_id = str(call.get("id", ""))
                    current["call_ids"].add(call_id)
                groups.append(current)
            elif role == "tool":
                call_id = str(message.get("tool_call_id", ""))
                attached = False
                if current is not None and call_id in current["call_ids"]:
                    current["results"].append(message)
                    attached = True
                if not attached:
                    for group in reversed(groups):
                        if call_id in group.get("call_ids", set()):
                            group["results"].append(message)
                            attached = True
                            break
                if not attached:
                    groups.append({"index": index, "message": message, "results": [], "call_ids": set()})
            else:
                groups.append({"index": index, "message": message, "results": [], "call_ids": set()})
        return groups

    def _partition_native_groups(self, groups):
        """按 budget 与 floor 切分 recent / older。"""
        budget_tokens = int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("recent_verbatim", 0.38)
        )
        recent = []
        used = 0
        # 先保 floor：最近 N 个完整 group。
        for group in reversed(groups):
            recent.append(group)
            used += self._count(self._group_text(group))
            if len(recent) >= self.recent_floor_groups:
                break
        recent.reverse()
        # 再按 budget 吸收更多 recent group（从后往前）。
        for group in reversed(groups):
            if any(item is group for item in recent):
                continue
            cost = self._count(self._group_text(group))
            if used + cost <= budget_tokens:
                recent.insert(0, group)
                used += cost
            else:
                break
        recent_ids = {id(item) for item in recent}
        older = [group for group in groups if id(group) not in recent_ids]
        return recent, older

    def _group_text(self, group):
        message = group.get("message", {})
        parts = [str(message.get("content") or "")]
        for call in message.get("tool_calls") or []:
            parts.append(
                f"{call.get('name', '')}({json.dumps(call.get('arguments', {}), sort_keys=True, ensure_ascii=True)})"
            )
        for result in group.get("results", []):
            parts.append(str(result.get("content", ""))[:400])
        return "\n".join(parts)

    def _compress_older_groups(self, older_groups, working_state):
        """对更旧 native groups 做结构化压缩（Stage C）。"""
        if not older_groups:
            return [], []
        raw_items = []
        for group in older_groups:
            message = group.get("message", {})
            raw_items.append(
                {
                    "role": str(message.get("role", "")),
                    "name": "",
                    "args": {},
                    "content": self._group_text(group),
                }
            )
        summary, meta = self.condenser.condense(
            raw_items, goal=working_state.goal, step=working_state.last_updated_step
        )
        if meta.get("mode") == "deterministic_fallback":
            self.compression_failure_count += 1
        self.compressed_summaries.append({"summary": summary, "meta": meta})
        return [
            ContextItem(
                key=f"condensed:{len(self.compressed_summaries)}",
                kind=ITEM_KIND_COMPRESSED_HISTORY,
                text=summary,
            )
        ], raw_items

    def _partition_history(self, history, working_state):
        """legacy：把 history 分为 older（可压缩）与 recent（原文）。"""
        budget_tokens = int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("recent_verbatim", 0.38)
        )
        recent = []
        older = []
        used = 0
        for item in reversed(history):
            cost = self._count(self._render_history_item(item))
            if used + cost <= budget_tokens or not older:
                recent.append(item)
                used += cost
            else:
                older.append(item)
        recent.reverse()
        older.reverse()
        if older:
            summary, meta = self.condenser.condense(
                older, goal=working_state.goal, step=working_state.last_updated_step
            )
            if meta.get("mode") == "deterministic_fallback":
                self.compression_failure_count += 1
            self.compressed_summaries.append({"summary": summary, "meta": meta})
            return [
                ContextItem(
                    key=f"condensed:{len(self.compressed_summaries)}",
                    kind=ITEM_KIND_COMPRESSED_HISTORY,
                    text=summary,
                )
            ], self._history_items(recent)
        return [], self._history_items(recent)

    @staticmethod
    def _render_compressed_items(items):
        return "\n\n".join(item.text for item in items)

    # ------------------------------------------------------------------
    # Repo Map
    # ------------------------------------------------------------------

    def _build_repo_map(self, user_message, working_state):
        budget_tokens = int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("repo_map", 0.10)
        )
        if self.repo_map_selector is None or budget_tokens <= 0:
            return [], {}
        blocks, details = self.repo_map_selector.select(
            user_message, working_state, budget_tokens, counter=self.token_counter
        )
        return [
            ContextItem(key=f"repo-map:{i}", kind=ITEM_KIND_REPO_MAP, text=block)
            for i, block in enumerate(blocks)
        ], details

    # ------------------------------------------------------------------
    # 组装与估算
    # ------------------------------------------------------------------

    def _assemble(self, pinned, working_state, recent_items, compressed_history_items, repo_map_items):
        parts = []
        pinned_text = self._render_pinned(pinned)
        if pinned_text:
            parts.append(pinned_text)
        working_text = working_state.to_text()
        if working_text.strip():
            parts.append(working_text)
        if compressed_history_items:
            parts.append(self._render_compressed_items(compressed_history_items))
        if repo_map_items:
            parts.append("Repository map:\n" + "\n".join(item.text for item in repo_map_items))
        if recent_items:
            parts.append("Transcript:\n" + "\n\n".join(item.text for item in recent_items))
        text = "\n\n".join(parts).strip()
        return CompiledContext(
            text=text,
            pinned=pinned,
            working_state=working_state,
            recent_items=recent_items,
            compressed_history_items=compressed_history_items,
            repo_map_items=repo_map_items,
        )

    def _estimate_candidate_tokens(self, pinned, working_state, history):
        text = self._render_pinned(pinned) + "\n" + working_state.to_text() + "\n"
        for item in history:
            text += self._render_history_item(item) + "\n"
        return self._count(text)

    def _estimate_native_tokens(self, messages):
        total = 0
        for message in messages:
            total += self._count(str(message.get("content") or ""))
            for call in message.get("tool_calls") or []:
                total += self._count(
                    str(call.get("name", ""))
                    + json.dumps(call.get("arguments", {}), sort_keys=True)
                )
        return total

    def _count(self, text):
        if self.token_counter is not None:
            try:
                return self.token_counter.count(text)
            except Exception:
                return len(str(text))
        return len(str(text))

    # ------------------------------------------------------------------
    # Observability metadata
    # ------------------------------------------------------------------

    def _metadata(self, **kwargs):
        native_mode = kwargs.pop("native_mode", False)
        compiled = kwargs.pop("compiled", None)
        user_message = kwargs.get("user_message", "")
        working_state = kwargs.get("working_state") or WorkingState()
        pinned = kwargs.get("pinned", [])
        recent_items = kwargs.get("recent_items", [])
        compressed_history_items = kwargs.get("compressed_history_items", [])
        repo_map_items = kwargs.get("repo_map_items", [])
        history = kwargs.get("history", [])
        should_compress = kwargs.get("should_compress", False)
        estimated = kwargs.get("estimated_tokens", 0)
        repo_map_details = kwargs.get("repo_map_details", {})
        compiled_text = compiled.text if compiled is not None else ""
        if native_mode:
            compiled_text = str(
                self._estimate_native_tokens(getattr(self, "last_native_messages", []) or [])
            )
        return {
            "compiler": "context_compiler",
            "native_mode": native_mode,
            "context_compile_count": self.compression_count + 1,
            "compression_count": self.compression_count,
            "compression_failure_count": self.compression_failure_count,
            "should_compress": should_compress,
            "candidate_context_tokens": estimated,
            "compiled_context_tokens": self._count(compiled_text),
            "pinned_tokens": sum(item.token_count(self.token_counter) for item in pinned),
            "working_state_tokens": self._count(working_state.to_text()),
            "recent_verbatim_tokens": sum(item.token_count(self.token_counter) for item in recent_items),
            "compressed_history_tokens": sum(item.token_count(self.token_counter) for item in compressed_history_items),
            "repo_map_tokens": sum(item.token_count(self.token_counter) for item in repo_map_items),
            "raw_history_tokens": (
                self._count(
                    "\n".join(self._render_history_item(item) for item in history)
                )
                if history
                else 0
            ),
            "fresh_fact_count": len(working_state.fresh_facts()),
            "stale_fact_count": len(working_state.stale_facts()),
            "estimated": True,
            "budget_source": self.budget.budget_source,
            "usable_input_budget": self.budget.usable_input_budget,
            "trigger_threshold": self.budget.trigger_threshold,
            "repo_map_selection": repo_map_details,
            "user_request": user_message,
        }


@dataclass
class CompiledContext:
    """一次编译的产物。"""

    text: str
    pinned: list = field(default_factory=list)
    working_state: Optional[WorkingState] = None
    recent_items: list = field(default_factory=list)
    compressed_history_items: list = field(default_factory=list)
    repo_map_items: list = field(default_factory=list)

    def to_dict(self):
        return {
            "text": self.text,
            "pinned_keys": [item.key for item in self.pinned],
            "recent_keys": [item.key for item in self.recent_items],
            "compressed_keys": [item.key for item in self.compressed_history_items],
            "repo_map_keys": [item.key for item in self.repo_map_items],
        }
