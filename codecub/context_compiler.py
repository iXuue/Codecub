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

import hashlib
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
# Phase 2.6 起由 hysteresis 的 high_watermark 取代（见下）；本常量保留为
# 旧默认的文档化别名，避免破坏依赖 DEFAULT_COMPRESSION_TRIGGER_THRESHOLD 的旧代码。
DEFAULT_COMPRESSION_TRIGGER_THRESHOLD = 0.75

# Phase 2.6 — Compression Hysteresis：
#   HIGH_WATERMARK -> compress -> TARGET_WATERMARK（留出 headroom）-> 重新涨到
#   HIGH 才再次压缩。
# 目标：不要再每步重复压同一批 history（Phase 2.5 Probe A 曾单 run 53 次压缩）。
DEFAULT_COMPRESSION_HIGH_WATERMARK = 0.80
DEFAULT_COMPRESSION_TARGET_WATERMARK = 0.55
DEFAULT_COMPRESSION_MIN_RECLAIM_TOKENS = 128
DEFAULT_COMPRESSION_MIN_RECLAIM_RATIO = 0.05
# 距上次压缩至少新增多少条 history 才允许再次压缩（防止同批 history 每步重压）。
DEFAULT_COMPRESSION_MIN_NEW_HISTORY_ENTRIES = 4

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
WORKING_STATE_MAX_READ_RANGES = 20

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
ITEM_KIND_RAW_EVIDENCE = "raw_evidence"
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
    read_ranges: list = field(default_factory=list)  # [{path, start_line, end_line, step, freshness}]
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
                self.invalidate_read_ranges_for_paths(changed)
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
            guard = metadata.get("read_range_guard") or {}
            requested = guard.get("requested_range") or []
            if path and len(requested) == 2 and guard.get("action") != "suppressed":
                self.add_read_range(
                    path,
                    requested[0],
                    requested[1],
                    step,
                    "stale_read" if guard.get("file_changed") else "unchanged",
                )
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

    def add_read_range(self, path, start_line, end_line, step=0, freshness="unchanged"):
        canonical = canonical_path(str(path))
        if not canonical:
            return
        entry = {
            "path": canonical,
            "start_line": int(start_line),
            "end_line": int(end_line),
            "step": int(step or 0),
            "freshness": str(freshness or "unchanged"),
        }
        self.read_ranges = [
            item
            for item in self.read_ranges
            if not (
                item.get("path") == canonical
                and item.get("start_line") == entry["start_line"]
                and item.get("end_line") == entry["end_line"]
            )
        ]
        self.read_ranges.append(entry)
        del self.read_ranges[:-WORKING_STATE_MAX_READ_RANGES]

    def invalidate_read_ranges_for_paths(self, changed_paths):
        changed = {canonical_path(str(path)) for path in (changed_paths or [])}
        self.read_ranges = [
            item for item in self.read_ranges if item.get("path") not in changed
        ]

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
        if self.read_ranges:
            lines.append("- Read Evidence:")
            for item in self.read_ranges[-WORKING_STATE_MAX_READ_RANGES:]:
                lines.append(
                    f"  * {item['path']} L{item['start_line']}-L{item['end_line']} "
                    f"@ step {item['step']}, {item['freshness']}"
                )
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
            "read_ranges": list(self.read_ranges),
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
        state.read_ranges = list((data or {}).get("read_ranges", []))[-WORKING_STATE_MAX_READ_RANGES:]
        state.last_updated_step = int((data or {}).get("last_updated_step", 0))
        return state


@dataclass
class _HysteresisState:
    """单条编译管线（legacy / native）的 hysteresis 运行状态。

    legacy 与 native 是两条独立管线，在同一 runtime 回合内都会跑（native 模式
    下 legacy compile_text 仍会为 observability 执行），因此压缩节流状态必须
    按管线隔离，避免两侧 history 长度口径互相污染。
    """

    steps_since_last_compression: int = 0
    compression_skipped_no_gain: int = 0
    compression_thrashing_detected: bool = False
    last_compressed_history_len: int = 0
    last_compressed_span_fingerprint: str = ""
    last_compiled_context_tokens: Optional[int] = None
    last_compression_compiled_tokens: Optional[int] = None


HYSTERESIS_MODES = ("legacy", "native")


@dataclass
class ContextBudget:
    """usable input budget 与压缩触发阈值（Phase 2.6 起带 Compression Hysteresis）。

    usable_input_budget = model_context_window - reserved_output_tokens
                          - tool_schema_overhead - safety_margin_tokens
    window 未知时使用 conservative fallback，并记录 budget_source。

    压缩决策（hysteresis，per-mode）：
        utilization >= high_watermark 且距上次压缩新增 history 达到门槛
            -> compress -> 目标是把 model-visible 降到 target_watermark 以下
        （留出 headroom）；此后要重新涨回 high_watermark 才再次压缩，
        不再“每步重复压同一批 history”。
    """

    usable_input_budget: int
    budget_source: str = "configured"  # configured | fallback
    context_window: Optional[int] = None
    reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS
    tool_schema_overhead: int = DEFAULT_TOOL_SCHEMA_OVERHEAD_TOKENS
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS
    # legacy 别名（已废弃）：显式传入时作为 high_watermark 使用；
    # 未传入时保持与 high_watermark 一致，供旧脚本读 trigger_threshold。
    trigger_threshold: Optional[float] = None
    # Phase 2.6 hysteresis 参数。
    high_watermark: float = DEFAULT_COMPRESSION_HIGH_WATERMARK
    target_watermark: float = DEFAULT_COMPRESSION_TARGET_WATERMARK
    min_reclaim_tokens: int = DEFAULT_COMPRESSION_MIN_RECLAIM_TOKENS
    min_reclaim_ratio: float = DEFAULT_COMPRESSION_MIN_RECLAIM_RATIO
    min_new_history_entries: int = DEFAULT_COMPRESSION_MIN_NEW_HISTORY_ENTRIES
    # Phase 2.6 hysteresis 运行状态（task-local，按管线隔离；
    # 由 compiler.reset_run_state() 经 reset_hysteresis() 清零）。
    _hysteresis: dict = field(
        default_factory=lambda: {mode: _HysteresisState() for mode in HYSTERESIS_MODES}
    )

    def __post_init__(self):
        if self.trigger_threshold is not None:
            # legacy 调用方（如 evaluator）显式传旧阈值：直接作为 high_watermark。
            self.high_watermark = float(self.trigger_threshold)
        else:
            self.trigger_threshold = self.high_watermark

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

    def _state(self, mode):
        mode = str(mode or "legacy")
        if mode not in self._hysteresis:
            self._hysteresis[mode] = _HysteresisState()
        return self._hysteresis[mode]

    def should_compress(self, estimated_tokens, history_len=0, history_fingerprint="", mode="legacy"):
        """Phase 2.6 hysteresis 压缩判定（per-mode）。

        单 threshold（utilization >= 0.75 -> compress）升级为：
          HIGH_WATERMARK -> compress -> TARGET_WATERMARK（headroom）-> 重新涨到
          HIGH 才再次压缩；
        并且距上次压缩新增 history 未达 min_new_history_entries（或压缩 span
        指纹未变）时跳过（compression_skipped_no_gain），避免同批 history 每步重压。

        说明：不设“compiled 超预算就无条件再压”的后门——对同一批 history 再压
        不产生增益（compiled 并不会因此变小），只会空转；真正的新内容由
        new_entries / span 指纹判断。
        """
        state = self._state(mode)
        state.steps_since_last_compression += 1
        if self.utilization(estimated_tokens) < self.high_watermark:
            return False
        if state.last_compressed_history_len:
            new_entries = max(0, int(history_len or 0) - state.last_compressed_history_len)
            if new_entries < self.min_new_history_entries:
                state.compression_skipped_no_gain += 1
                return False
            if (
                history_fingerprint
                and state.last_compressed_span_fingerprint
                and history_fingerprint == state.last_compressed_span_fingerprint
            ):
                # 完全同一批 history span：压缩无增益。
                state.compression_skipped_no_gain += 1
                return False
        return True

    def mark_compressed(self, history_len, history_fingerprint, compiled_tokens, estimated_tokens, mode="legacy"):
        """一次压缩真实发生后的登记；返回本次压缩的回收统计（per-mode）。"""
        state = self._state(mode)
        state.steps_since_last_compression = 0
        state.last_compressed_history_len = int(history_len or 0)
        state.last_compressed_span_fingerprint = str(history_fingerprint or "")
        state.last_compression_compiled_tokens = compiled_tokens
        reclaimed = max(0, int(estimated_tokens or 0) - int(compiled_tokens or 0))
        ratio = reclaimed / int(estimated_tokens) if estimated_tokens else 0.0
        if (
            compiled_tokens
            and estimated_tokens
            and (reclaimed < self.min_reclaim_tokens or ratio < self.min_reclaim_ratio)
        ):
            # 压缩跑了但几乎没回收：同一批 history 的压缩没有产生有效空间。
            # （注意：native 模式下 recent floor 会保留整段 tool result，
            #  compiled 常驻接近可用预算属于结构性现象，不作为 thrash 依据。）
            state.compression_thrashing_detected = True
        return {"reclaimed_tokens": reclaimed, "reduction_ratio": ratio}

    def note_compiled(self, compiled_tokens, mode="legacy"):
        """记录一次编译的 model-visible 大小（per-mode，供 observability）。"""
        self._state(mode).last_compiled_context_tokens = compiled_tokens
        return self

    def reset_hysteresis(self, mode=None):
        """task-local：每次 ask 开始清零（与 Working State 生命周期一致）。

        mode=None 时重置全部管线。
        """
        if mode is None:
            for name in list(self._hysteresis):
                self._hysteresis[name] = _HysteresisState()
            return self
        self._hysteresis[str(mode)] = _HysteresisState()
        return self

    def hysteresis_snapshot(self, mode="legacy"):
        state = self._state(mode)
        return {
            "high_watermark": self.high_watermark,
            "target_watermark": self.target_watermark,
            "min_reclaim_tokens": self.min_reclaim_tokens,
            "min_reclaim_ratio": self.min_reclaim_ratio,
            "min_new_history_entries": self.min_new_history_entries,
            "steps_since_last_compression": state.steps_since_last_compression,
            "compression_skipped_no_gain": state.compression_skipped_no_gain,
            "compression_thrashing_detected": state.compression_thrashing_detected,
            "last_compressed_history_len": state.last_compressed_history_len,
            "last_compressed_span_fingerprint": state.last_compressed_span_fingerprint,
            "last_compiled_context_tokens": state.last_compiled_context_tokens,
            "last_compression_compiled_tokens": state.last_compression_compiled_tokens,
        }


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

    def condense(self, history, goal="", step=0, max_output_tokens=None, excluded_facts=None):
        """把旧 history 压缩为结构化 summary。失败时回退 deterministic 且保留 raw。

        返回 (summary_text, meta)：
        - summary_text 是压缩后的结构化文本；
        - meta 记录 mode（llm/deterministic/fallback）、错误、token 估算。
        """
        raw_count = len(history)
        deterministic = self._deterministic_condense(
            history, goal, step, max_output_tokens, excluded_facts
        )
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

    @staticmethod
    def _normalize_fact(text):
        return re.sub(r"[\W_]+", "", str(text).lower())

    def _extract_high_signal_facts(self, text, role):
        if role not in {"user", "assistant"}:
            return []
        facts = []
        # Do not split on every period: it is common inside file and symbol
        # names (for example ``retrieval.py``).  A period is a sentence
        # boundary here only when followed by whitespace and an uppercase
        # sentence start; the other sentence terminators are unambiguous.
        for fragment in re.split(
            r"\n+|(?<=[!?。！？])\s+|(?<=\.)\s+(?=[A-Z])", str(text or "")
        ):
            value = " ".join(fragment.strip().split())
            if not value:
                continue
            lower = value.lower()
            if re.search(r"\b(do not|don't|must not|leave .+ unchanged)\b|禁止修改|不要修改|不得修改", lower):
                category = "Constraints"
            elif re.search(r"\b(must|require|should continue to|must remain|keep .+ compatible)\b|必须|需要|保持.*兼容", lower):
                category = "Requirements"
            elif re.search(r"\b(decision:|use .+ rather than|rather than)\b|决定|使用.+而不是", lower):
                category = "Decisions"
            elif re.search(r"\b(we confirmed|confirmed|caused by|not caused by|relevant implementation)\b|已确认|问题.*原因|不是.*导致", lower):
                category = "Confirmed Facts"
            elif re.search(r"\b(before|after|only|exactly|at least|at most|and|or|not)\b", lower):
                category = "Logical Constraints"
            else:
                continue
            facts.append((category, value[:360]))
        return facts

    def _fit_sections(self, sections, max_output_tokens):
        lines = []
        for title, entries in sections:
            if not entries and title != "Earlier Investigation:":
                continue
            candidate = [title] + [f"- {entry}" for entry in entries]
            if max_output_tokens is None:
                lines.extend(candidate)
                continue
            if self.token_counter is None:
                fits_title = len("\n".join(lines + [title])) <= max_output_tokens
            else:
                fits_title = self.token_counter.count("\n".join(lines + [title])) <= max_output_tokens
            if not fits_title:
                break
            lines.append(title)
            for entry in entries:
                candidate_text = "\n".join(lines + [f"- {entry}"])
                count = self.token_counter.count(candidate_text) if self.token_counter else len(candidate_text)
                if count > max_output_tokens:
                    return "\n".join(lines)
                lines.append(f"- {entry}")
        return "\n".join(lines)

    def _deterministic_condense(self, history, goal, step, max_output_tokens=None, excluded_facts=None):
        findings, changed, verification, failed, decisions, symbols = [], [], [], [], [], []
        high_signal = {
            "Constraints": [], "Requirements": [], "Decisions": [],
            "Confirmed Facts": [], "Logical Constraints": [],
        }
        excluded = set(excluded_facts or [])
        seen_signal = set()
        seen_files = set()
        redact = self.redact_fn or (lambda text: text)
        for index, item in enumerate(history):
            role = str(item.get("role", ""))
            name = str(item.get("name", ""))
            args = item.get("args") or {}
            content = str(item.get("content", ""))
            for category, fact in self._extract_high_signal_facts(content, role):
                normalized = self._normalize_fact(fact)
                if normalized and normalized not in seen_signal and normalized not in excluded:
                    seen_signal.add(normalized)
                    high_signal[category].append(redact(fact))
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
        sections = []
        for category in (
            "Constraints", "Requirements", "Decisions", "Confirmed Facts", "Logical Constraints"
        ):
            if high_signal[category]:
                sections.append((f"[{category}]", high_signal[category]))
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
        if not (findings or changed or verification or failed or any(high_signal.values())):
            lines.append("- (no structured signals captured)")
        tool_sections = [
            ("[Tool Evidence]", [line.lstrip("- ").strip() for line in lines[1:]])
        ]
        return self._fit_sections(
            [("Earlier Investigation:", [])] + sections + tool_sections,
            max_output_tokens,
        ) or "Earlier Investigation:\n- (compression budget exhausted)"

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
        hybrid_context_enabled=False,
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
        # State-Preserving Compression 是 production default；Hybrid Raw Evidence
        # 为 experimental / opt-in，且不改变 condenser 或 layer ratios。
        self.hybrid_context_enabled = bool(hybrid_context_enabled)
        # 状态（每轮 compile 更新，供 observability）。
        self.last_compile_metadata = {}
        self.compression_count = 0
        self.compression_failure_count = 0
        self.compressed_summaries = []  # recursive condensation 栈

    # ------------------------------------------------------------------
    # Task-local 生命周期
    # ------------------------------------------------------------------

    def reset_run_state(self):
        """每次 ask() 开始时清零 task-local 状态（与 Working State 生命周期一致）。

        清零项：压缩计数 / 压缩失败计数 / summary 栈 / 上一次编译产物 /
        hysteresis 运行状态。不触碰 pinned/working state 之外的对象状态。
        """
        self.last_compile_metadata = {}
        self.compression_count = 0
        self.compression_failure_count = 0
        self.compressed_summaries = []
        self.last_native_messages = None
        if self.budget is not None:
            self.budget.reset_hysteresis()
        return self

    def _history_span_fingerprint(self, history):
        """标识“会被压缩的 history span”（最旧的 1/3），用于跳过同批重复压缩。

        新条目不断追加时，最旧 1/3 只在旧条目滚出该段时变化，因此能识别
        “同一批旧 history 被反复重压”的情况（observability + exact-replay skip）。
        """
        if not history:
            return ""
        span = history[: max(1, len(history) // 3)]
        parts = []
        for item in span:
            if item.get("role") == "tool":
                parts.append(
                    f"{item.get('name', '')}:{json.dumps(item.get('args', {}), sort_keys=True)}"
                )
            else:
                parts.append(str(item.get("content", ""))[:60])
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def compile_text(self, user_message, working_state=None, history=None, pinned_extra=None, memory_layer=None, memory_meta=None):
        """legacy text 模式：从 session history 编译文本 prompt。"""
        working_state = working_state or WorkingState()
        history = list(history or [])
        pinned_extra = pinned_extra or {}
        memory_meta = memory_meta or {}
        pinned = self._build_pinned(user_message, pinned_extra)
        estimated = self._estimate_candidate_tokens(pinned, working_state, history)
        history_fingerprint = self._history_span_fingerprint(history)
        over_high = self.budget.utilization(estimated) >= self.budget.high_watermark
        should_compress = self.budget.should_compress(
            estimated, len(history), history_fingerprint, mode="legacy"
        )
        compressed_history = []
        raw_evidence_items = []
        hybrid_details = self._empty_hybrid_details()
        recent_items = []
        if should_compress:
            self.compression_count += 1
            compressed_history, raw_evidence_items, recent_items, hybrid_details = self._partition_history(
                history, working_state, user_message, pinned, memory_layer
            )
        elif over_high:
            # Phase 2.6 hysteresis skip：仍分区 + 压缩，保持 model-visible 有界
            # （避免两次计次压缩之间上下文无界增长），但不计为一次 compression
            # （不触发 checkpoint / 不重置 span / 不计数）。
            compressed_history, raw_evidence_items, recent_items, hybrid_details = self._partition_history(
                history, working_state, user_message, pinned, memory_layer
            )
        else:
            recent_items = self._history_items(history)
        repo_map_items, repo_map_details = self._build_repo_map(user_message, working_state)
        compiled = self._assemble(
            pinned=pinned,
            working_state=working_state,
            recent_items=recent_items,
            raw_evidence_items=raw_evidence_items,
            compressed_history_items=compressed_history,
            repo_map_items=repo_map_items,
            memory_layer=memory_layer,
        )
        compiled_tokens = self._count(compiled.text)
        self.budget.note_compiled(compiled_tokens, mode="legacy")
        if should_compress:
            self.budget.mark_compressed(
                len(history), history_fingerprint, compiled_tokens, estimated, mode="legacy"
            )
        self.last_compile_metadata = self._metadata(
            user_message=user_message,
            working_state=working_state,
            history=history,
            pinned=pinned,
            recent_items=recent_items,
            raw_evidence_items=raw_evidence_items,
            compressed_history_items=compressed_history,
            repo_map_items=repo_map_items,
            repo_map_details=repo_map_details,
            should_compress=should_compress,
            estimated_tokens=estimated,
            compiled=compiled,
            memory_layer=memory_layer,
            memory_meta=memory_meta,
            hybrid_details=hybrid_details,
        )
        return compiled.text, self.last_compile_metadata

    def compile_native(self, user_message, working_state=None, native_messages=None, pinned_extra=None, memory_layer=None, memory_meta=None):
        """native 模式：压缩 native_messages，保持 assistant.tool_calls + tool result 原子性。

        返回 (messages, metadata)；messages 始终以合法 native 顺序：
        system / user / assistant(tool_calls) 后紧跟对应 tool 结果。
        """
        working_state = working_state or WorkingState()
        original_messages = native_messages
        native_messages = list(native_messages or [])
        pinned_extra = pinned_extra or {}
        memory_meta = memory_meta or {}
        pinned = self._build_pinned(user_message, pinned_extra)
        estimated = self._estimate_native_tokens(native_messages)
        history_fingerprint = self._history_span_fingerprint(native_messages)
        over_high = self.budget.utilization(estimated) >= self.budget.high_watermark
        should_compress = self.budget.should_compress(
            estimated, len(native_messages), history_fingerprint, mode="native"
        )
        if not should_compress and not over_high:
            # 未超 HIGH：消息量小，无需压缩；返回原列表引用，保持 runtime 的
            # 追加语义不变。
            self.last_native_messages = native_messages
            self.budget.note_compiled(estimated, mode="native")
            self.last_compile_metadata = self._metadata(
                user_message=user_message,
                working_state=working_state,
                history=native_messages,
                pinned=pinned,
                recent_items=self._native_items(native_messages),
                raw_evidence_items=[],
                compressed_history_items=[],
                repo_map_items=[],
                repo_map_details={},
                should_compress=False,
                estimated_tokens=estimated,
                compiled=None,
                native_mode=True,
                memory_layer=memory_layer,
                memory_meta=memory_meta,
                hybrid_details=self._empty_hybrid_details(),
            )
            return (
                original_messages if original_messages is not None else native_messages,
                self.last_compile_metadata,
            )
        counted = should_compress
        if counted:
            self.compression_count += 1
        groups = self._group_native_messages(native_messages)
        recent_groups, older_groups = self._partition_native_groups(groups)
        preliminary_history_items, preliminary_raw = self._compress_older_groups(
            older_groups, working_state
        )
        reclaim_baseline = self._estimate_native_tokens(self._assemble_native_messages(
            pinned, working_state, memory_layer, preliminary_history_items, [], [], recent_groups
        ))
        raw_evidence_groups, older_groups, hybrid_details = self._select_native_raw_evidence(
            older_groups, working_state, user_message, reclaim_baseline
        )
        if raw_evidence_groups:
            compressed_history_items, older_raw = self._compress_older_groups(
                older_groups, working_state
            )
        else:
            compressed_history_items, older_raw = preliminary_history_items, preliminary_raw
        repo_map_items, repo_map_details = self._build_repo_map(user_message, working_state)
        messages = self._assemble_native_messages(
            pinned, working_state, memory_layer, compressed_history_items,
            repo_map_items, raw_evidence_groups, recent_groups,
        )
        messages, raw_evidence_groups, recent_groups, older_groups, compressed_history_items, budget_enforced = (
            self._enforce_native_budget(
                messages,
                pinned,
                working_state,
                memory_layer,
                repo_map_items,
                raw_evidence_groups,
                recent_groups,
                older_groups,
                compressed_history_items,
            )
        )
        self.last_native_messages = messages
        selected_signatures = {
            group.get("_hybrid_signature", "") for group in raw_evidence_groups
        }
        hybrid_details["selected"] = [
            item for item in hybrid_details["selected"]
            if item.get("signature", "") in selected_signatures
        ]
        hybrid_details["raw_reclaim_tokens"] = sum(
            self._estimate_native_group_tokens(group) for group in raw_evidence_groups
        )
        compiled_tokens = self._estimate_native_tokens(messages)
        self.budget.note_compiled(compiled_tokens, mode="native")
        if counted:
            self.budget.mark_compressed(
                len(native_messages), history_fingerprint, compiled_tokens, estimated, mode="native"
            )
        self.last_compile_metadata = self._metadata(
            user_message=user_message,
            working_state=working_state,
            history=native_messages,
            pinned=pinned,
            recent_items=self._native_items(recent_groups),
            raw_evidence_items=self._native_items(raw_evidence_groups, kind=ITEM_KIND_RAW_EVIDENCE),
            raw_evidence_token_count=sum(
                self._estimate_native_group_tokens(group) for group in raw_evidence_groups
            ),
            compressed_history_items=compressed_history_items,
            repo_map_items=repo_map_items,
            repo_map_details=repo_map_details,
            should_compress=counted,
            estimated_tokens=estimated,
            compiled=None,
            native_mode=True,
            older_raw_entries=len(older_raw),
            memory_layer=memory_layer,
            memory_meta=memory_meta,
            budget_enforcement_triggered=budget_enforced,
            hybrid_details=hybrid_details,
        )
        return messages, self.last_compile_metadata

    def _assemble_native_messages(
        self, pinned, working_state, memory_layer, compressed_history_items,
        repo_map_items, raw_evidence_groups, recent_groups,
    ):
        """Render exactly the native message fields that will be provider-bound."""

        messages = []
        pinned_text = self._render_pinned(pinned)
        working_text = working_state.to_text()
        summary_text = self._render_compressed_items(compressed_history_items)
        memory_text = str(memory_layer or "").strip()
        preamble_parts = []
        if pinned_text:
            preamble_parts.append(pinned_text)
        if working_text.strip():
            preamble_parts.append(working_text)
        if memory_text:
            preamble_parts.append(memory_text)
        if summary_text.strip():
            preamble_parts.append(summary_text)
        if repo_map_items:
            preamble_parts.append(
                "Repository map:\n"
                + "\n".join(item.text for item in repo_map_items)
            )
        if preamble_parts:
            messages.append(
                {
                    "role": "system",
                    "content": "\n\n".join(preamble_parts),
                }
            )
        for group in raw_evidence_groups:
            messages.append(group["message"])
            messages.extend(group.get("results", []))
        for group in recent_groups:
            messages.append(group["message"])
            messages.extend(group.get("results", []))
        return messages

    def _enforce_native_budget(
        self, messages, pinned, working_state, memory_layer, repo_map_items,
        raw_evidence_groups, recent_groups, older_groups, compressed_history_items,
    ):
        """Final provider-bound invariant; trim oldest mutable atomic groups first."""

        enforced = False
        while (
            self._estimate_native_tokens(messages) > self.budget.usable_input_budget
            and (raw_evidence_groups or recent_groups)
        ):
            if raw_evidence_groups:
                # 先移除最低分 raw evidence，并交回既有 condenser；never split a
                # native tool-call/result group.
                removable_index = min(
                    range(len(raw_evidence_groups)),
                    key=lambda index: (
                        raw_evidence_groups[index].get("_hybrid_score", 0),
                        raw_evidence_groups[index].get("index", 0),
                    ),
                )
                enforced = True
                older_groups = [raw_evidence_groups.pop(removable_index), *older_groups]
                compressed_history_items, _ = self._compress_older_groups(
                    older_groups, working_state
                )
                messages = self._assemble_native_messages(
                    pinned, working_state, memory_layer, compressed_history_items,
                    repo_map_items, raw_evidence_groups, recent_groups,
                )
                continue
            removable_index = next(
                (
                    index
                    for index, group in enumerate(recent_groups)
                    if str(group.get("message", {}).get("role", "")) not in {"system", "user"}
                ),
                None,
            )
            if removable_index is None:
                break
            enforced = True
            older_groups = [recent_groups.pop(removable_index), *older_groups]
            compressed_history_items, _ = self._compress_older_groups(
                older_groups, working_state
            )
            messages = self._assemble_native_messages(
                pinned, working_state, memory_layer, compressed_history_items,
                repo_map_items, raw_evidence_groups, recent_groups,
            )
        return (
            messages,
            raw_evidence_groups,
            recent_groups,
            older_groups,
            compressed_history_items,
            enforced,
        )

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

    def _native_items(self, groups, kind=ITEM_KIND_RECENT_VERBATIM):
        items = []
        for group in groups:
            message = group.get("message", {})
            text = str(message.get("content") or message.get("tool_calls") or "")
            items.append(
                ContextItem(
                    key=f"native:{group.get('index', 0)}",
                    kind=kind,
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
            used += self._estimate_native_group_tokens(group)
            if len(recent) >= self.recent_floor_groups:
                break
        recent.reverse()
        # 再按 budget 吸收更多 recent group（从后往前）。
        for group in reversed(groups):
            if any(item is group for item in recent):
                continue
            cost = self._estimate_native_group_tokens(group)
            if used + cost <= budget_tokens:
                recent.insert(0, group)
                used += cost
            else:
                break
        recent_ids = {id(item) for item in recent}
        older = [group for group in groups if id(group) not in recent_ids]
        return recent, older

    @staticmethod
    def _empty_hybrid_details():
        return {
            "candidates": [], "candidate_count": 0, "selected": [],
            "dropped": [], "raw_reclaim_tokens": 0,
        }

    def _safe_raw_reclaim_limit(self, baseline_tokens):
        """Use existing usable budget and HIGH watermark; never fill usable budget."""
        safe_target = int(
            self.budget.usable_input_budget * self.budget.high_watermark
        )
        return max(0, safe_target - int(baseline_tokens or 0))

    @staticmethod
    def _normalized_path(value):
        return canonical_path(str(value or ""))

    def _evidence_descriptor(self, entry, index, native=False):
        """Extract deterministic, local-only evidence from one atomic entry/group."""
        if native:
            message = entry.get("message", {})
            calls = message.get("tool_calls") or []
            tools = []
            for call in calls:
                function = call.get("function") or call
                arguments = function.get("arguments", {}) or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                tools.append((str(function.get("name", "")), dict(arguments)))
            content = "\n".join(
                [str(message.get("content", ""))]
                + [str(result.get("content", "")) for result in entry.get("results", [])]
            )
            role = str(message.get("role", ""))
        else:
            tools = [(str(entry.get("name", "")), dict(entry.get("args", {}) or {}))]
            content = str(entry.get("content", ""))
            role = str(entry.get("role", ""))
        paths = {
            self._normalized_path(args.get("path", ""))
            for _, args in tools
            if args.get("path")
        }
        paths.discard("")
        names = {name for name, _ in tools if name}
        signature = hashlib.sha256(
            ("|".join(sorted(names)) + "|" + "|".join(sorted(paths)) + "|" + content[:800]).encode("utf-8")
        ).hexdigest()[:16]
        return {
            "entry": entry, "index": index, "native": native, "role": role,
            "tools": names, "paths": paths, "content": content,
            "signature": signature,
        }

    def _score_raw_evidence(self, descriptor, user_message, working_state):
        """Explainable evidence score.  Stale and duplicate evidence is never raw."""
        reasons = []
        score = 0
        paths = descriptor["paths"]
        changed = {self._normalized_path(path) for path in working_state.changed_files}
        changed.discard("")
        relevant = changed | {
            self._normalized_path(item.get("path", ""))
            for item in working_state.relevant_symbols + working_state.read_ranges
        }
        relevant.discard("")
        task_lower = str(user_message or "").lower()
        content_lower = descriptor["content"].lower()
        tool_names = descriptor["tools"]
        if paths & changed and "read_file" in tool_names:
            return 0, ["stale_changed_file"], "stale"
        if paths & relevant:
            score += 4
            reasons.append("current_target_file")
        if any(path and path.lower() in task_lower for path in paths):
            score += 3
            reasons.append("task_path_affinity")
        if "run_shell" in tool_names and (
            "traceback" in content_lower or "assertionerror" in content_lower
            or "exit_code: 1" in content_lower or "failed" in content_lower
        ):
            score += 5
            reasons.append("recent_failed_test")
        if "patch_file" in tool_names or "write_file" in tool_names:
            if paths & changed:
                score += 3
                reasons.append("current_patch_evidence")
        if "read_file" in tool_names and paths & relevant:
            score += 2
            reasons.append("fresh_read_current_target")
        if descriptor["role"] == "user" and any(
            marker in content_lower for marker in ("do not", "must", "compatibility", "contract")
        ):
            score += 3
            reasons.append("explicit_constraint_context")
        return score, reasons, "fresh"

    def _select_raw_descriptors(self, descriptors, user_message, working_state, baseline_tokens, cost_for):
        details = self._empty_hybrid_details()
        if not self.hybrid_context_enabled:
            return [], descriptors, details
        latest_by_signature = {}
        for descriptor in descriptors:
            latest_by_signature[descriptor["signature"]] = descriptor["index"]
        candidates = []
        remainder = []
        for descriptor in descriptors:
            score, reasons, freshness = self._score_raw_evidence(
                descriptor, user_message, working_state
            )
            debug = {
                "index": descriptor["index"], "score": score,
                "reasons": reasons, "freshness": freshness,
            }
            if latest_by_signature[descriptor["signature"]] != descriptor["index"]:
                details["dropped"].append({**debug, "reason": "duplicate"})
                remainder.append(descriptor)
                continue
            if freshness == "stale":
                details["dropped"].append({**debug, "reason": "stale"})
                remainder.append(descriptor)
                continue
            if score <= 0:
                remainder.append(descriptor)
                continue
            descriptor["_hybrid_score"] = score
            descriptor["_hybrid_reasons"] = reasons
            descriptor["_hybrid_freshness"] = freshness
            descriptor["_hybrid_cost"] = cost_for(descriptor)
            details["candidates"].append({**debug, "tokens": descriptor["_hybrid_cost"]})
            candidates.append(descriptor)
        limit = self._safe_raw_reclaim_limit(baseline_tokens)
        selected = []
        used = 0
        for descriptor in sorted(candidates, key=lambda item: (-item["_hybrid_score"], -item["index"])):
            cost = descriptor["_hybrid_cost"]
            if used + cost <= limit:
                selected.append(descriptor)
                used += cost
            else:
                remainder.append(descriptor)
                details["dropped"].append({
                    "index": descriptor["index"], "score": descriptor["_hybrid_score"],
                    "reasons": descriptor["_hybrid_reasons"], "freshness": descriptor["_hybrid_freshness"],
                    "reason": "reclaim_limit",
                })
        selected.sort(key=lambda item: item["index"])
        selected_ids = {id(item) for item in selected}
        remainder.extend(item for item in candidates if id(item) not in selected_ids and item not in remainder)
        details["candidate_count"] = len(candidates)
        details["selected"] = [
            {"index": item["index"], "score": item["_hybrid_score"],
             "reasons": item["_hybrid_reasons"], "freshness": item["_hybrid_freshness"],
             "tokens": item["_hybrid_cost"], "signature": item["signature"]}
            for item in selected
        ]
        details["raw_reclaim_tokens"] = used
        return selected, sorted(remainder, key=lambda item: item["index"]), details

    def _select_native_raw_evidence(self, older_groups, working_state, user_message, baseline_tokens):
        descriptors = [
            self._evidence_descriptor(group, index, native=True)
            for index, group in enumerate(older_groups)
        ]
        selected, remainder, details = self._select_raw_descriptors(
            descriptors, user_message, working_state, baseline_tokens,
            lambda item: self._estimate_native_group_tokens(item["entry"]),
        )
        selected_groups = []
        for item in selected:
            group = item["entry"]
            group["_hybrid_score"] = item["_hybrid_score"]
            group["_hybrid_reasons"] = item["_hybrid_reasons"]
            group["_hybrid_freshness"] = item["_hybrid_freshness"]
            group["_hybrid_signature"] = item["signature"]
            selected_groups.append(group)
        return selected_groups, [item["entry"] for item in remainder], details

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

    def _estimate_native_group_tokens(self, group):
        """Provider-bound accounting for a full atomic native group, without previews."""

        return self._estimate_native_tokens(
            [group.get("message", {}), *(group.get("results", []) or [])]
        )

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
                    "content": str(message.get("content", "")),
                }
            )
            for call, result in zip(
                message.get("tool_calls") or [], group.get("results", []) or []
            ):
                function = call.get("function") or call
                arguments = function.get("arguments", {}) or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                raw_items.append(
                    {
                        "role": "tool",
                        "name": str(function.get("name", "")),
                        "args": arguments,
                        "content": str(result.get("content", ""))[:400],
                    }
                )
        summary, meta = self.condenser.condense(
            raw_items,
            goal=working_state.goal,
            step=working_state.last_updated_step,
            max_output_tokens=self._compressed_history_budget(),
            excluded_facts=self._working_state_fact_keys(working_state),
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

    def _compressed_history_budget(self):
        return int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("compressed_history", 0.20)
        )

    @staticmethod
    def _working_state_fact_keys(working_state):
        return {
            HistoryCondenser._normalize_fact(item.get("text", ""))
            for item in getattr(working_state, "known_facts", [])
            if item.get("text")
        }

    def _partition_history(self, history, working_state, user_message, pinned, memory_layer):
        """legacy：把 history 分为 older（可压缩）与 recent（原文）。

        Phase 2.6 修正：recent 必须受 recent_verbatim 层预算约束（至少保留
        最新 1 条原文），超出部分进入 older 压缩；旧的 `or not older` 条件会让
        整段 history 都留在 recent，导致 legacy “压缩” 空转（compiled ≈ raw，
        同口径 metrics 暴露为 reclaimed=0）。
        """
        budget_tokens = int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("recent_verbatim", 0.38)
        )
        recent = []
        older = []
        used = 0
        for item in reversed(history):
            cost = self._count(self._render_history_item(item))
            if used + cost <= budget_tokens or not recent:
                recent.append(item)
                used += cost
            else:
                older.append(item)
        recent.reverse()
        older.reverse()
        details = self._empty_hybrid_details()
        raw_evidence_items = []
        if older:
            summary, meta = self.condenser.condense(
                older,
                goal=working_state.goal,
                step=working_state.last_updated_step,
                max_output_tokens=self._compressed_history_budget(),
                excluded_facts=self._working_state_fact_keys(working_state),
            )
            if meta.get("mode") == "deterministic_fallback":
                self.compression_failure_count += 1
            self.compressed_summaries.append({"summary": summary, "meta": meta})
            compressed_items = [
                ContextItem(
                    key=f"condensed:{len(self.compressed_summaries)}",
                    kind=ITEM_KIND_COMPRESSED_HISTORY,
                    text=summary,
                )
            ]
            preliminary = self._assemble(
                pinned, working_state, self._history_items(recent), [],
                compressed_items, [], memory_layer,
            )
            descriptors = [
                self._evidence_descriptor(item, index, native=False)
                for index, item in enumerate(older)
            ]
            selected, remainder, details = self._select_raw_descriptors(
                descriptors, user_message, working_state, self._count(preliminary.text),
                lambda item: self._count(self._render_history_item(item["entry"])),
            )
            if selected:
                raw_evidence_items = [
                    ContextItem(
                        key=f"raw-evidence:{item['index']}",
                        kind=ITEM_KIND_RAW_EVIDENCE,
                        text=self._render_history_item(item["entry"]),
                        provenance={"why_raw_preserved": item["_hybrid_reasons"], "freshness": item["_hybrid_freshness"]},
                    )
                    for item in selected
                ]
                remaining_history = [item["entry"] for item in remainder]
                summary, meta = self.condenser.condense(
                    remaining_history,
                    goal=working_state.goal,
                    step=working_state.last_updated_step,
                    max_output_tokens=self._compressed_history_budget(),
                    excluded_facts=self._working_state_fact_keys(working_state),
                )
                if meta.get("mode") == "deterministic_fallback":
                    self.compression_failure_count += 1
                self.compressed_summaries.append({"summary": summary, "meta": meta})
                compressed_items = [
                    ContextItem(
                        key=f"condensed:{len(self.compressed_summaries)}",
                        kind=ITEM_KIND_COMPRESSED_HISTORY,
                        text=summary,
                    )
                ]
            return compressed_items, raw_evidence_items, self._history_items(recent), details
        return [], raw_evidence_items, self._history_items(recent), details

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

    def _assemble(self, pinned, working_state, recent_items, raw_evidence_items, compressed_history_items, repo_map_items, memory_layer=None):
        parts = []
        pinned_text = self._render_pinned(pinned)
        if pinned_text:
            parts.append(pinned_text)
        working_text = working_state.to_text()
        if working_text.strip():
            parts.append(working_text)
        memory_text = str(memory_layer or "").strip()
        if memory_text:
            parts.append(memory_text)
        if compressed_history_items:
            parts.append(self._render_compressed_items(compressed_history_items))
        if raw_evidence_items:
            parts.append(
                "High-value raw evidence:\n"
                + "\n\n".join(item.text for item in raw_evidence_items)
            )
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
            raw_evidence_items=raw_evidence_items,
            compressed_history_items=compressed_history_items,
            repo_map_items=repo_map_items,
        )

    def _estimate_candidate_tokens(self, pinned, working_state, history):
        text = self._render_pinned(pinned) + "\n" + working_state.to_text() + "\n"
        for item in history:
            text += self._render_history_item(item) + "\n"
        return self._count(text)

    def _estimate_native_tokens(self, messages):
        """Estimate the full serialized payload sent to a native provider."""

        return sum(
            self._count(
                json.dumps(
                    message, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), default=str,
                )
            )
            for message in messages
        )

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
        raw_evidence_items = kwargs.get("raw_evidence_items", [])
        compressed_history_items = kwargs.get("compressed_history_items", [])
        repo_map_items = kwargs.get("repo_map_items", [])
        history = kwargs.get("history", [])
        should_compress = kwargs.get("should_compress", False)
        estimated = kwargs.get("estimated_tokens", 0)
        repo_map_details = kwargs.get("repo_map_details", {})
        memory_layer = kwargs.get("memory_layer", None)
        memory_meta = kwargs.get("memory_meta", {}) or {}
        budget_enforcement_triggered = bool(
            kwargs.get("budget_enforcement_triggered", False)
        )
        hybrid_details = kwargs.get("hybrid_details", self._empty_hybrid_details())
        memory_text = str(memory_layer or "").strip()
        if native_mode:
            compiled_tokens = self._estimate_native_tokens(
                getattr(self, "last_native_messages", []) or []
            )
        else:
            compiled_tokens = self._count(compiled.text if compiled is not None else "")
        pinned_tokens = sum(item.token_count(self.token_counter) for item in pinned)
        working_state_tokens = self._count(working_state.to_text())
        recent_verbatim_tokens = sum(
            item.token_count(self.token_counter) for item in recent_items
        )
        raw_evidence_tokens = kwargs.get("raw_evidence_token_count")
        if raw_evidence_tokens is None:
            raw_evidence_tokens = sum(
                item.token_count(self.token_counter) for item in raw_evidence_items
            )
        compressed_history_tokens = sum(
            item.token_count(self.token_counter) for item in compressed_history_items
        )
        repo_map_tokens = sum(
            item.token_count(self.token_counter) for item in repo_map_items
        )
        # Phase 2.6 — 同口径 token metrics：raw / compiled 覆盖同一范围，ratio 才可算。
        if native_mode:
            raw_history_tokens = self._estimate_native_tokens(history)
            compiled_history_tokens = self._estimate_native_tokens(
                getattr(self, "last_native_messages", []) or []
            )
            raw_model_visible = raw_history_tokens
            compiled_model_visible = compiled_history_tokens
        else:
            raw_history_tokens = (
                self._count(
                    "\n".join(self._render_history_item(item) for item in history)
                )
                if history
                else 0
            )
            compiled_history_tokens = (
                recent_verbatim_tokens + raw_evidence_tokens + compressed_history_tokens
            )
            raw_model_visible = (
                pinned_tokens + working_state_tokens + raw_history_tokens + repo_map_tokens
            )
            compiled_model_visible = compiled_tokens
        context_reclaimed = max(0, raw_model_visible - compiled_model_visible)
        context_reduction_ratio = (
            context_reclaimed / raw_model_visible if raw_model_visible else 0.0
        )
        history_reclaimed = max(0, raw_history_tokens - compiled_history_tokens)
        history_reduction_ratio = (
            history_reclaimed / raw_history_tokens if raw_history_tokens else 0.0
        )
        hysteresis = (
            self.budget.hysteresis_snapshot(mode="native" if native_mode else "legacy")
            if self.budget is not None
            else {}
        )
        usable_budget = self.budget.usable_input_budget if self.budget is not None else None
        budget_overflow = max(0, compiled_tokens - usable_budget) if usable_budget else 0
        return {
            "compiler": "context_compiler",
            "native_mode": native_mode,
            "context_compile_count": self.compression_count + 1,
            "compression_count": self.compression_count,
            "compression_failure_count": self.compression_failure_count,
            "should_compress": should_compress,
            "candidate_context_tokens": estimated,
            "compiled_context_tokens": compiled_tokens,
            "provider_bound_prompt_tokens": compiled_tokens if native_mode else None,
            "budget_overflow_tokens": budget_overflow if native_mode else 0,
            "budget_enforcement_triggered": budget_enforcement_triggered,
            "native_group_estimation_mode": (
                "provider_bound_serialized_messages" if native_mode else "not_applicable"
            ),
            "pinned_tokens": pinned_tokens,
            "working_state_tokens": working_state_tokens,
            "recent_verbatim_tokens": recent_verbatim_tokens,
            "hybrid_context_enabled": self.hybrid_context_enabled,
            "raw_evidence_candidates": hybrid_details.get("candidate_count", 0),
            "raw_evidence_selected": hybrid_details.get("selected", []),
            "raw_evidence_selected_count": len(hybrid_details.get("selected", [])),
            "raw_evidence_tokens": raw_evidence_tokens,
            "compressed_history_tokens": compressed_history_tokens,
            "dropped_history_groups": hybrid_details.get("dropped", []),
            "raw_reclaim_tokens": hybrid_details.get("raw_reclaim_tokens", 0),
            "unused_context_budget": max(0, (usable_budget or 0) - compiled_tokens),
            "final_provider_bound_tokens": compiled_tokens if native_mode else None,
            "repo_map_tokens": repo_map_tokens,
            "raw_history_tokens": raw_history_tokens,
            "compiled_history_tokens": compiled_history_tokens,
            "history_reduction_ratio": history_reduction_ratio,
            "raw_model_visible_tokens": raw_model_visible,
            "compiled_model_visible_tokens": compiled_model_visible,
            "context_tokens_reclaimed": context_reclaimed,
            "context_reduction_ratio": context_reduction_ratio,
            "provider_actual_input_tokens": None,  # runtime 在拿到 usage 后回填
            "fresh_fact_count": len(working_state.fresh_facts()),
            "stale_fact_count": len(working_state.stale_facts()),
            "estimated": True,
            "budget_source": self.budget.budget_source if self.budget is not None else "",
            "usable_input_budget": usable_budget,
            # legacy 别名：旧脚本读 trigger_threshold；实际决策用 high_watermark。
            "trigger_threshold": (
                self.budget.high_watermark if self.budget is not None else None
            ),
            "hysteresis": hysteresis,
            "repo_map_selection": repo_map_details,
            # Phase 3: bounded Retrieved Memory layer.
            "memory_layer_rendered": bool(memory_text),
            "memory_tokens": self._count(memory_text) if memory_text else 0,
            "memory_evidence_count": int(memory_meta.get("evidence_count", 0)),
            "memory_durable_count": int(memory_meta.get("durable_count", 0)),
            "memory_stale_count": int(memory_meta.get("stale_count", 0)),
            "memory_token_budget": memory_meta.get("token_budget"),
            "user_request": user_message,
        }


@dataclass
class CompiledContext:
    """一次编译的产物。"""

    text: str
    pinned: list = field(default_factory=list)
    working_state: Optional[WorkingState] = None
    recent_items: list = field(default_factory=list)
    raw_evidence_items: list = field(default_factory=list)
    compressed_history_items: list = field(default_factory=list)
    repo_map_items: list = field(default_factory=list)

    def to_dict(self):
        return {
            "text": self.text,
            "pinned_keys": [item.key for item in self.pinned],
            "recent_keys": [item.key for item in self.recent_items],
            "raw_evidence_keys": [item.key for item in self.raw_evidence_items],
            "compressed_keys": [item.key for item in self.compressed_history_items],
            "repo_map_keys": [item.key for item in self.repo_map_items],
        }
