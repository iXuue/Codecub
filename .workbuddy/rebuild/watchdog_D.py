"""Progress Watchdog — 独立的 stuck 检测与 recovery 状态机。

Phase 1 的定位：它是 runtime 里“是否卡住”的唯一决策来源（single source of
truth）。所有旧 hard-stop 状态机（repeated_no_progress、semantic repeat hard
stop 等）都收敛到本模块，runtime 只消费本模块给出的 WatchdogDecision。

设计原则：
- 输入只来自 runtime 的客观事件：工具名、参数、执行结果元数据、step 号。
  不使用 LLM 自评，因此可确定性测试。
- progress 必须是“真实进展”：新文件、新 symbol、新的非重叠证据、workspace
  修改、新的测试/错误信号、edit decision 实质变化。工具调用成功本身不算。
- stuck 不直接等于失败：第一次触发只进入 STUCK_SUSPECTED，由 runtime 启动
  一次 Recovery Turn；Recovery 后出现真实 progress 恢复 NORMAL，仍无进展才
  进入 STUCK_CONFIRMED。

状态机：

    NORMAL
      ↓ 窗口内无真实进展 / 检测到循环模式
    STUCK_SUSPECTED  (runtime 注入 Recovery Turn)
      ↓
    RECOVERY
      ↓ 出现真实 progress                    ↓ Recovery 窗口内仍无进展
    NORMAL                                STUCK_CONFIRMED
"""

from collections import deque
from dataclasses import dataclass, field
import json
import re

from .task_policy import (
    canonical_path,
    normalize_search,
    normalize_shell_command,
    read_overlap_ratio,
)

WATCHDOG_STATE_NORMAL = "normal"
WATCHDOG_STATE_STUCK_SUSPECTED = "stuck_suspected"
WATCHDOG_STATE_RECOVERY = "recovery"
WATCHDOG_STATE_STUCK_CONFIRMED = "stuck_confirmed"

# 集中配置，避免 magic number 散落在 runtime 里。
DEFAULT_WATCHDOG_WINDOW = 6
DEFAULT_NO_PROGRESS_WINDOW_LIMIT = 6
DEFAULT_IDENTICAL_LOOP_LIMIT = 5
DEFAULT_ALTERNATING_LOOP_LIMIT = 6
DEFAULT_VERIFICATION_LOOP_LIMIT = 4
DEFAULT_ACTION_ERROR_LOOP_LIMIT = 4
DEFAULT_RECOVERY_WINDOW = 6
DEFAULT_SEMANTIC_READ_OVERLAP = 0.8

# ProgressSignal.kind 的取值。
PROGRESS_NEW_FILE = "new_file"
PROGRESS_NEW_SYMBOL = "new_symbol"
PROGRESS_NEW_EVIDENCE = "new_evidence"
PROGRESS_WORKSPACE_CHANGE = "workspace_change"
PROGRESS_NEW_VERIFICATION = "new_verification"
PROGRESS_NEW_TEST_RESULT = "new_test_result"
PROGRESS_NEW_ERROR = "new_error"
PROGRESS_EDIT_PROGRESS = "edit_progress"
PROGRESS_VERIFICATION_STATE = "verification_state_change"
PROGRESS_BLOCKER_CHANGE = "blocker_change"

# Stuck pattern 的取值。
PATTERN_IDENTICAL_LOOP = "identical_loop"
PATTERN_SEMANTIC_READ_LOOP = "semantic_read_loop"
PATTERN_SEARCH_LOOP = "search_loop"
PATTERN_ACTION_ERROR_LOOP = "action_error_loop"
PATTERN_ALTERNATING_LOOP = "alternating_loop"
PATTERN_VERIFICATION_LOOP = "verification_loop"
PATTERN_NO_PROGRESS_WINDOW = "no_progress_window"

_EXIT_CODE_PATTERN = re.compile(r"exit_code:\s*(-?\d+)")


@dataclass
class ProgressSignal:
    """一次客观 progress 事件。"""

    kind: str
    reason: str
    step: int


@dataclass
class WatchdogDecision:
    """runtime 每步从 watchdog 拿到的决策结果。"""

    state: str
    progress_signals: list = field(default_factory=list)
    stuck_pattern: str = ""
    suspected_now: bool = False
    recovered_now: bool = False
    confirmed_now: bool = False


def _error_signature(metadata, result_text):
    """从工具结果元数据里提取稳定错误签名，供 Action/Error、Verification loop 去重。"""
    status = str((metadata or {}).get("tool_status", "")).strip()
    code = str((metadata or {}).get("tool_error_code", "")).strip()
    if status not in {"error", "rejected"} and not code:
        return ""
    match = _EXIT_CODE_PATTERN.search(str(result_text or ""))
    exit_code = match.group(1) if match else ""
    return f"{status}|{code}|{exit_code}"


def _args_signature(args):
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        return str(args)


class ProgressWatchdog:
    """跟踪最近工具事件，判定是否卡住，并驱动 suspected/recovery/confirmed 状态机。

    runtime 的用法（概念）::

        decision = watchdog.record_tool_event(name, args, metadata, result_text, step)
        if decision.suspected_now:
            watchdog.begin_recovery(step)
            # runtime: 注入 Recovery Turn 提示，不停止
        elif decision.recovered_now:
            # runtime: 记录 recovery_success
        elif decision.confirmed_now:
            # runtime: stop（stop_reason=stuck_confirmed）
    """

    def __init__(
        self,
        window=DEFAULT_WATCHDOG_WINDOW,
        no_progress_window_limit=DEFAULT_NO_PROGRESS_WINDOW_LIMIT,
        identical_loop_limit=DEFAULT_IDENTICAL_LOOP_LIMIT,
        alternating_loop_limit=DEFAULT_ALTERNATING_LOOP_LIMIT,
        verification_loop_limit=DEFAULT_VERIFICATION_LOOP_LIMIT,
        action_error_loop_limit=DEFAULT_ACTION_ERROR_LOOP_LIMIT,
        recovery_window=DEFAULT_RECOVERY_WINDOW,
        semantic_read_overlap=DEFAULT_SEMANTIC_READ_OVERLAP,
    ):
        self.window = int(window)
        self.no_progress_window_limit = int(no_progress_window_limit)
        self.identical_loop_limit = int(identical_loop_limit)
        self.alternating_loop_limit = int(alternating_loop_limit)
        self.verification_loop_limit = int(verification_loop_limit)
        self.action_error_loop_limit = int(action_error_loop_limit)
        self.recovery_window = int(recovery_window)
        self.semantic_read_overlap = float(semantic_read_overlap)
        maxlen = (
            max(
                self.window,
                self.identical_loop_limit,
                self.alternating_loop_limit,
                self.verification_loop_limit,
                self.action_error_loop_limit,
                self.recovery_window,
            )
            + 2
        )
        self.events = deque(maxlen=maxlen)
        self.state = WATCHDOG_STATE_NORMAL
        self.last_progress_step = 0
        self.last_progress_reason = ""
        self.no_progress_score = 0
        self.stuck_suspected_count = 0
        self.recovery_turn_count = 0
        self.recovery_success_count = 0
        self.stuck_confirmed_count = 0
        self.current_pattern = ""
        self._recovery_start_step = None
        self._seen_files = set()
        self._seen_symbol_queries = set()
        self._seen_searches = set()
        self._seen_shell_commands = set()
        self._seen_error_signatures = set()
        self._failed_commands = set()
        self._verification_loop_streak = 0
        self._action_error_streak = 0
        self._workspace_epoch = 0

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------

    def record_tool_event(self, name, args, metadata, result_text, step):
        """记录一次工具执行，返回当前决策。

        `metadata` 是 runtime 的 `_last_tool_result_metadata`（工具状态、错误码、
        affected_paths、workspace_changed 等客观字段）；`result_text` 是裁剪后的
        工具结果文本，用于提取错误签名。
        """
        event = {
            "step": step,
            "name": name,
            "sig": (name, _args_signature(args)),
            "args": dict(args or {}),
            "workspace_changed": bool((metadata or {}).get("workspace_changed")),
            "error_sig": _error_signature(metadata, result_text),
            "status": str((metadata or {}).get("tool_status", "")),
        }
        if name == "search":
            event["search_sig"] = normalize_search(args)
        self.events.append(event)

        signals = self._detect_progress(name, args, metadata, result_text, step)
        has_progress = bool(signals)
        if has_progress:
            self._reset_no_progress()
            signal = signals[-1]
            self.last_progress_step = signal.step
            self.last_progress_reason = signal.reason
            self.current_pattern = ""
        else:
            self.no_progress_score += 1
        self._track_streaks(event)

        pattern = self._detect_stuck_pattern()
        decision = WatchdogDecision(state=self.state, progress_signals=signals)

        if self.state == WATCHDOG_STATE_NORMAL:
            if pattern or self.no_progress_score >= self.no_progress_window_limit:
                self.state = WATCHDOG_STATE_STUCK_SUSPECTED
                self.stuck_suspected_count += 1
                self.current_pattern = pattern or PATTERN_NO_PROGRESS_WINDOW
                decision.state = self.state
                decision.stuck_pattern = self.current_pattern
                decision.suspected_now = True
        elif self.state == WATCHDOG_STATE_RECOVERY:
            if has_progress:
                self.state = WATCHDOG_STATE_NORMAL
                self.recovery_success_count += 1
                decision.state = self.state
                decision.recovered_now = True
            elif self._recovery_elapsed(step) >= self.recovery_window:
                self.state = WATCHDOG_STATE_STUCK_CONFIRMED
                self.stuck_confirmed_count += 1
                decision.state = self.state
                decision.stuck_pattern = (
                    self.current_pattern or PATTERN_NO_PROGRESS_WINDOW
                )
                decision.confirmed_now = True
        return decision

    def record_progress(self, kind, reason, step):
        """显式 progress 信号（例如 blocker / verification 状态变化）。"""
        self._reset_no_progress()
        self.last_progress_step = step
        self.last_progress_reason = reason
        self.current_pattern = ""
        decision = WatchdogDecision(state=self.state)
        if self.state == WATCHDOG_STATE_RECOVERY:
            self.state = WATCHDOG_STATE_NORMAL
            self.recovery_success_count += 1
            decision.state = self.state
            decision.recovered_now = True
        return decision

    def begin_recovery(self, step):
        """进入 Recovery Turn。由 runtime 在 suspected_now 后调用。"""
        self.state = WATCHDOG_STATE_RECOVERY
        self.recovery_turn_count += 1
        self._recovery_start_step = step
        self.no_progress_score = 0

    def snapshot(self):
        return {
            "state": self.state,
            "window": self.window,
            "no_progress_window_limit": self.no_progress_window_limit,
            "recovery_window": self.recovery_window,
            "last_progress_step": self.last_progress_step,
            "last_progress_reason": self.last_progress_reason,
            "no_progress_score": self.no_progress_score,
            "stuck_suspected_count": self.stuck_suspected_count,
            "recovery_turn_count": self.recovery_turn_count,
            "recovery_success_count": self.recovery_success_count,
            "stuck_confirmed_count": self.stuck_confirmed_count,
            "stuck_pattern": self.current_pattern,
        }

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _reset_no_progress(self):
        self.no_progress_score = 0
        self._verification_loop_streak = 0
        self._action_error_streak = 0

    def _recovery_elapsed(self, step):
        if self._recovery_start_step is None:
            return 0
        return max(0, step - self._recovery_start_step)

    def _detect_progress(self, name, args, metadata, result_text, step):
        """从一次工具事件里识别真实 progress 信号。"""
        signals = []
        if bool((metadata or {}).get("workspace_changed")):
            self._workspace_epoch += 1
            signals.append(
                ProgressSignal(
                    PROGRESS_WORKSPACE_CHANGE,
                    f"workspace modified (epoch {self._workspace_epoch})",
                    step,
                )
            )
            return signals
        if str((metadata or {}).get("tool_status", "")) == "rejected":
            return signals
        if name == "read_file":
            path = canonical_path(args.get("path"))
            if path not in self._seen_files:
                self._seen_files.add(path)
                signals.append(
                    ProgressSignal(PROGRESS_NEW_FILE, f"first read of {path}", step)
                )
                return signals
            if self._is_fresh_read(path, args):
                signals.append(
                    ProgressSignal(
                        PROGRESS_NEW_EVIDENCE,
                        f"non-overlapping evidence in {path}",
                        step,
                    )
                )
            return signals
        if name == "symbol_search":
            query = (
                canonical_path(args.get("path", ".")),
                str(args.get("query", "")).strip().casefold(),
            )
            if query not in self._seen_symbol_queries:
                self._seen_symbol_queries.add(query)
                signals.append(
                    ProgressSignal(
                        PROGRESS_NEW_SYMBOL,
                        f"symbol lookup {args.get('query', '')!r}",
                        step,
                    )
                )
            return signals
        if name == "search":
            signature = normalize_search(args)
            if signature not in self._seen_searches:
                self._seen_searches.add(signature)
                signals.append(
                    ProgressSignal(
                        PROGRESS_NEW_EVIDENCE,
                        "new search over the workspace",
                        step,
                    )
                )
            return signals
        if name == "run_shell":
            command = normalize_shell_command(args)
            error_sig = _error_signature(metadata, result_text)
            if command not in self._seen_shell_commands:
                self._seen_shell_commands.add(command)
                if error_sig:
                    self._seen_error_signatures.add(error_sig)
                    self._failed_commands.add(command)
                signals.append(
                    ProgressSignal(
                        PROGRESS_NEW_VERIFICATION,
                        f"first verification command {command[:60]!r}",
                        step,
                    )
                )
                return signals
            if error_sig:
                if error_sig not in self._seen_error_signatures:
                    self._seen_error_signatures.add(error_sig)
                    self._failed_commands.add(command)
                    signals.append(
                        ProgressSignal(
                            PROGRESS_NEW_ERROR,
                            "new failure signature from verification",
                            step,
                        )
                    )
                # else: 同命令、同错误签名重复 -> verification loop 信号，无 progress
            elif command in self._failed_commands:
                # 同命令从失败变为成功 -> 新测试结果
                signals.append(
                    ProgressSignal(
                        PROGRESS_NEW_TEST_RESULT,
                        "verification command now succeeds",
                        step,
                    )
                )
            return signals
        return signals

    def _is_fresh_read(self, path, args):
        """同文件是否读取了新的非高度重叠区间（当前事件已入队，比较历史）。"""
        for prior in self.events:
            if prior["step"] == self.events[-1]["step"]:
                continue
            if prior["name"] != "read_file":
                continue
            if canonical_path(prior["args"].get("path")) != path:
                continue
            overlap = read_overlap_ratio(args, prior["args"])
            if overlap >= self.semantic_read_overlap:
                return False
        return True

    def _track_streaks(self, event):
        """维护 verification / action-error 连续 streak。"""
        if event["error_sig"] and not event["workspace_changed"]:
            if event["name"] == "run_shell":
                self._verification_loop_streak += 1
                self._action_error_streak = 0
            elif event["name"] in {"patch_file", "write_file"}:
                self._action_error_streak += 1
                self._verification_loop_streak = 0
        else:
            self._verification_loop_streak = 0
            self._action_error_streak = 0

    def _detect_stuck_pattern(self):
        if self._detect_identical_loop():
            return PATTERN_IDENTICAL_LOOP
        if self._detect_alternating_loop():
            return PATTERN_ALTERNATING_LOOP
        if self._verification_loop_streak >= self.verification_loop_limit:
            return PATTERN_VERIFICATION_LOOP
        if self._action_error_streak >= self.action_error_loop_limit:
            return PATTERN_ACTION_ERROR_LOOP
        if self._detect_semantic_read_loop():
            return PATTERN_SEMANTIC_READ_LOOP
        if self._detect_search_loop():
            return PATTERN_SEARCH_LOOP
        return ""

    def _detect_identical_loop(self):
        recent = [event for event in self.events]
        if len(recent) < self.identical_loop_limit:
            return False
        last = recent[-self.identical_loop_limit:]
        first_sig = last[0]["sig"]
        return all(event["sig"] == first_sig for event in last)

    def _detect_alternating_loop(self):
        recent = [event for event in self.events]
        if len(recent) < self.alternating_loop_limit:
            return False
        last = recent[-self.alternating_loop_limit:]
        first, second = last[0]["sig"], last[1]["sig"]
        if first == second:
            return False
        for index, event in enumerate(last):
            expected = first if index % 2 == 0 else second
            if event["sig"] != expected:
                return False
        return True

    def _detect_semantic_read_loop(self):
        """同文件高重叠区间在窗口内反复读取（复用 task_policy 的重叠逻辑）。

        滑动读取（如 1-11, 2-12, 3-13, ...）相邻区间高度重叠但都与第一个区间
        逐渐错开，因此按相邻事件两两比较，而不是全部相对第一个。
        """
        recent = [event for event in self.events if event["name"] == "read_file"]
        if len(recent) < self.identical_loop_limit:
            return False
        last = recent[-self.identical_loop_limit:]
        path = canonical_path(last[0]["args"].get("path"))
        if not path:
            return False
        for previous, current in zip(last, last[1:]):
            if canonical_path(current["args"].get("path")) != path:
                return False
            if (
                read_overlap_ratio(previous["args"], current["args"])
                < self.semantic_read_overlap
            ):
                return False
        return True

    def _detect_search_loop(self):
        """相同 normalized search 在窗口内反复出现。"""
        recent = [event for event in self.events if event["name"] == "search"]
        if len(recent) < self.identical_loop_limit:
            return False
        last = recent[-self.identical_loop_limit:]
        return len({event.get("search_sig") for event in last}) == 1
