"""EditDecisionWatchdog — progress-aware edit-decision gatekeeper（Phase 2.6）。

取代“第几次 edit decision 就 hard-stop”的小固定预算
（EDIT_DECISION_ATTEMPT_BUDGET / EDIT_EVIDENCE_RETRY_BUDGET）：

原则
----
> 是否继续由“有没有真实进展”决定，而不是由“这是第几次 decision”决定。

- “edit” 决策永远放行（真实 mutation 由 Workspace Snapshot / Verifier 客观验证）。
- “need_evidence” 决策逐条分类：
    progress：
      - 新文件（该路径首次 read）
      - 新 range（同文件新的非高度重叠区间）
      - 新 symbol（新的 symbol_search query）
      - 新 search（新的 normalized search signature）
      - mutation 后同文件 hash 变化的 re-read（stale -> revalidation -> fresh）
    no progress：
      - 完全相同 / 高度重叠的 read、相同 search、相同 symbol query，
        且其间没有 workspace change、文件 hash 也未变化。

职责边界
--------
- 本模块只做“这一次 evidence 请求是否带来新信息”的分类与观测计数；
- 不负责 stuck 判定（那仍由 ProgressWatchdog 的 suspected -> recovery ->
  confirmed 状态机决定；本模块的 no-progress 事件会以 rejected tool 事件
  形式喂给主 Watchdog，保证重复 evidence 也能走完整 recovery 链路）；
- 不删除任何工具调用机会：允许的 evidence 工具集合
  （read_file / search / symbol_search）由 runtime 保持，本模块不扩权。
"""

from __future__ import annotations

from dataclasses import dataclass

from .task_policy import canonical_path, normalize_search, read_overlap_ratio

READ_OVERLAP_THRESHOLD = 0.8
MAX_TRACKED_READ_RANGES_PER_PATH = 32
MAX_TRACKED_SEARCHES = 64
MAX_TRACKED_SYMBOLS = 64

# EvidenceClassification.kind 取值。
EVIDENCE_NEW_FILE = "new_file"
EVIDENCE_NEW_RANGE = "new_range"
EVIDENCE_NEW_SYMBOL = "new_symbol"
EVIDENCE_NEW_SEARCH = "new_search"
EVIDENCE_RE_READ_AFTER_CHANGE = "re_read_after_change"
EVIDENCE_REPEATED = "repeated_evidence"


@dataclass
class EvidenceClassification:
    """一次 need_evidence 请求的分类结果。"""

    progress: bool
    kind: str
    reason: str = ""
    step: int = 0


class EditDecisionWatchdog:
    """跟踪 edit-decision 阶段的客观证据进展。

    runtime 用法（概念）::

        classification = watchdog.classify_evidence_request(name, args, step)
        if classification.progress:
            watchdog.mark_evidence_executed(name, args, step)
            # runtime: 执行受控 read/search/symbol 工具
        else:
            watchdog.record_no_progress(classification)
            # runtime: 拒绝该请求（retry notice），并把 no-progress 事件
            #          喂给主 ProgressWatchdog（suspected -> recovery -> confirmed）
    """

    def __init__(self, file_hash_fn=None):
        self.file_hash_fn = file_hash_fn
        self.total_decisions = 0
        self.edit_decisions = 0
        self.evidence_decisions = 0
        self.evidence_executed = 0
        self.evidence_rejected_no_progress = 0
        self.no_progress_streak = 0
        self.last_progress_kind = ""
        self.last_progress_step = 0
        self._seen_reads = {}  # canonical path -> [{start, end, step}]
        self._seen_searches = set()
        self._seen_symbols = set()
        self._file_hashes = {}  # canonical path -> hash at last executed read

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------

    def reset(self):
        """新 ask 开始时清零（task-local）。"""
        self.__init__(file_hash_fn=self.file_hash_fn)

    def record_decision(self, kind):
        self.total_decisions += 1
        if kind == "edit":
            self.edit_decisions += 1
        else:
            self.evidence_decisions += 1
        return self

    def classify_evidence_request(self, name, args, step):
        """分类一次 need_evidence 请求：是否带来真实新信息。"""
        name = str(name or "")
        args = dict(args or {})
        if name == "read_file":
            return self._classify_read(args, step)
        if name == "search":
            signature = normalize_search(args)
            if signature in self._seen_searches:
                return EvidenceClassification(
                    False, EVIDENCE_REPEATED, "identical search already executed", step
                )
            return EvidenceClassification(
                True, EVIDENCE_NEW_SEARCH, "new search over the workspace", step
            )
        if name == "symbol_search":
            signature = (
                canonical_path(args.get("path", ".")),
                str(args.get("query", "")).strip().casefold(),
            )
            if signature in self._seen_symbols:
                return EvidenceClassification(
                    False, EVIDENCE_REPEATED, "identical symbol query already executed", step
                )
            return EvidenceClassification(
                True, EVIDENCE_NEW_SYMBOL, f"new symbol lookup {args.get('query', '')!r}", step
            )
        # 不在受控 evidence 集合内的工具由 runtime 的安全边界拒绝，这里不扩权。
        return EvidenceClassification(
            True, "unclassified", f"evidence tool {name} not classified", step
        )

    def mark_evidence_executed(self, name, args, step):
        """evidence 请求被真实执行后登记，供后续重复检测。"""
        name = str(name or "")
        args = dict(args or {})
        self.evidence_executed += 1
        self.last_progress_kind = self._kind_for(name, args)
        self.last_progress_step = int(step or 0)
        self.no_progress_streak = 0
        if name == "read_file":
            path = canonical_path(args.get("path"))
            if path:
                ranges = self._seen_reads.setdefault(path, [])
                ranges.append(
                    {
                        "start": int(args.get("start", 1)),
                        "end": int(args.get("end", 200)),
                        "step": int(step or 0),
                    }
                )
                del ranges[:-MAX_TRACKED_READ_RANGES_PER_PATH]
                current = self._file_hash(path)
                if current:
                    self._file_hashes[path] = current
        elif name == "search":
            self._seen_searches.add(normalize_search(args))
            if len(self._seen_searches) > MAX_TRACKED_SEARCHES:
                self._seen_searches = set(sorted(self._seen_searches)[-MAX_TRACKED_SEARCHES:])
        elif name == "symbol_search":
            self._seen_symbols.add(
                (
                    canonical_path(args.get("path", ".")),
                    str(args.get("query", "")).strip().casefold(),
                )
            )
            if len(self._seen_symbols) > MAX_TRACKED_SYMBOLS:
                self._seen_symbols = set(sorted(self._seen_symbols)[-MAX_TRACKED_SYMBOLS:])
        return self

    def record_no_progress(self, classification):
        self.evidence_rejected_no_progress += 1
        self.no_progress_streak += 1
        return self

    def snapshot(self):
        return {
            "total_decisions": self.total_decisions,
            "edit_decisions": self.edit_decisions,
            "evidence_decisions": self.evidence_decisions,
            "evidence_executed": self.evidence_executed,
            "evidence_rejected_no_progress": self.evidence_rejected_no_progress,
            "no_progress_streak": self.no_progress_streak,
            "last_progress_kind": self.last_progress_kind,
            "last_progress_step": self.last_progress_step,
            "file_hash_tracking": self.file_hash_fn is not None,
        }

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _classify_read(self, args, step):
        path = canonical_path(args.get("path"))
        if not path:
            return EvidenceClassification(
                True, EVIDENCE_NEW_FILE, "read without canonical path", step
            )
        current_hash = self._file_hash(path)
        previous_hash = self._file_hashes.get(path)
        if (
            previous_hash is not None
            and current_hash is not None
            and current_hash != previous_hash
        ):
            # mutation 后同文件 hash 变化的 re-read：stale -> revalidation -> fresh。
            return EvidenceClassification(
                True,
                EVIDENCE_RE_READ_AFTER_CHANGE,
                f"re-read of {path} after file change",
                step,
            )
        prior_ranges = self._seen_reads.get(path, [])
        if not prior_ranges:
            return EvidenceClassification(
                True, EVIDENCE_NEW_FILE, f"first read of {path}", step
            )
        if not any(
            read_overlap_ratio(args, prior) >= READ_OVERLAP_THRESHOLD
            for prior in prior_ranges
        ):
            return EvidenceClassification(
                True, EVIDENCE_NEW_RANGE, f"non-overlapping evidence in {path}", step
            )
        return EvidenceClassification(
            False,
            EVIDENCE_REPEATED,
            f"identical/overlapping read of {path} with no file change",
            step,
        )

    @staticmethod
    def _kind_for(name, args):
        if name == "read_file":
            return EVIDENCE_NEW_FILE
        if name == "search":
            return EVIDENCE_NEW_SEARCH
        if name == "symbol_search":
            return EVIDENCE_NEW_SYMBOL
        return "evidence"

    def _file_hash(self, path):
        if self.file_hash_fn is None:
            return None
        try:
            return self.file_hash_fn(path)
        except Exception:
            return None
