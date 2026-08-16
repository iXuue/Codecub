"""Phase 2.6 — Adaptive Edit Control（EditDecisionWatchdog）专项测试。

覆盖：
- EditDecisionWatchdog 单元分类（新文件 / 新 range / 新 symbol / 新 search /
  mutation 后 hash 变化的 re-read / 完全重复无进展）；
- runtime 集成：新 evidence 可以继续超过旧 4 次 edit-decision hard-stop、
  超过旧 2 次 evidence budget；
- runtime 集成：重复 evidence -> 拒绝 -> 主 Watchdog suspected -> recovery ->
  stuck_confirmed（不再 edit_decision_exhausted）。
"""

import json

from codecub.edit_decision import (
    EVIDENCE_NEW_FILE,
    EVIDENCE_NEW_RANGE,
    EVIDENCE_NEW_SEARCH,
    EVIDENCE_NEW_SYMBOL,
    EVIDENCE_RE_READ_AFTER_CHANGE,
    EVIDENCE_REPEATED,
    EditDecisionWatchdog,
)
from codecub.memory import file_freshness
from codecub.models import ModelResponse, ToolCall
from codecub.runtime import STOP_REASON_STUCK_CONFIRMED
from codecub import MiniAgent, SessionStore, WorkspaceContext


# ---------------------------------------------------------------------------
# EditDecisionWatchdog 单元分类
# ---------------------------------------------------------------------------


def test_new_file_read_is_progress():
    watchdog = EditDecisionWatchdog()
    cls = watchdog.classify_evidence_request(
        "read_file", {"path": "a.py", "start": 1, "end": 10}, 1
    )
    assert cls.progress and cls.kind == EVIDENCE_NEW_FILE


def test_new_range_is_progress():
    watchdog = EditDecisionWatchdog()
    watchdog.mark_evidence_executed("read_file", {"path": "a.py", "start": 1, "end": 10}, 1)
    cls = watchdog.classify_evidence_request(
        "read_file", {"path": "a.py", "start": 11, "end": 20}, 2
    )
    assert cls.progress and cls.kind == EVIDENCE_NEW_RANGE


def test_identical_read_is_no_progress():
    watchdog = EditDecisionWatchdog()
    watchdog.mark_evidence_executed("read_file", {"path": "a.py", "start": 1, "end": 10}, 1)
    cls = watchdog.classify_evidence_request(
        "read_file", {"path": "a.py", "start": 1, "end": 10}, 2
    )
    assert not cls.progress and cls.kind == EVIDENCE_REPEATED


def test_overlapping_read_is_no_progress():
    watchdog = EditDecisionWatchdog()
    watchdog.mark_evidence_executed("read_file", {"path": "a.py", "start": 1, "end": 10}, 1)
    cls = watchdog.classify_evidence_request(
        "read_file", {"path": "a.py", "start": 2, "end": 10}, 2
    )
    assert not cls.progress and cls.kind == EVIDENCE_REPEATED


def test_rereread_after_file_change_is_progress(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("alpha\n", encoding="utf-8")
    watchdog = EditDecisionWatchdog(
        file_hash_fn=lambda path: file_freshness(path, tmp_path)
    )
    watchdog.mark_evidence_executed("read_file", {"path": "a.py", "start": 1, "end": 10}, 1)
    same = watchdog.classify_evidence_request(
        "read_file", {"path": "a.py", "start": 1, "end": 10}, 2
    )
    assert not same.progress  # 未变化：完全重复
    target.write_text("beta\n", encoding="utf-8")
    changed = watchdog.classify_evidence_request(
        "read_file", {"path": "a.py", "start": 1, "end": 10}, 3
    )
    assert changed.progress and changed.kind == EVIDENCE_RE_READ_AFTER_CHANGE


def test_new_search_is_progress():
    watchdog = EditDecisionWatchdog()
    watchdog.mark_evidence_executed("search", {"path": ".", "pattern": "old"}, 1)
    cls = watchdog.classify_evidence_request("search", {"path": ".", "pattern": "old"}, 2)
    assert not cls.progress and cls.kind == EVIDENCE_REPEATED
    cls = watchdog.classify_evidence_request(
        "search", {"path": ".", "pattern": "baseline"}, 3
    )
    assert cls.progress and cls.kind == EVIDENCE_NEW_SEARCH


def test_new_symbol_is_progress():
    watchdog = EditDecisionWatchdog()
    watchdog.mark_evidence_executed("symbol_search", {"path": ".", "query": "Pico"}, 1)
    cls = watchdog.classify_evidence_request(
        "symbol_search", {"path": ".", "query": "Pico"}, 2
    )
    assert not cls.progress and cls.kind == EVIDENCE_REPEATED
    cls = watchdog.classify_evidence_request(
        "symbol_search", {"path": ".", "query": "Watchdog"}, 3
    )
    assert cls.progress and cls.kind == EVIDENCE_NEW_SYMBOL


def test_counters_and_snapshot():
    watchdog = EditDecisionWatchdog()
    watchdog.record_decision("edit")
    watchdog.record_decision("need_evidence")
    watchdog.mark_evidence_executed("read_file", {"path": "a.py", "start": 1, "end": 5}, 1)
    watchdog.record_no_progress(
        watchdog.classify_evidence_request("read_file", {"path": "a.py", "start": 1, "end": 5}, 2)
    )
    snapshot = watchdog.snapshot()
    assert snapshot["total_decisions"] == 2
    assert snapshot["edit_decisions"] == 1
    assert snapshot["evidence_decisions"] == 1
    assert snapshot["evidence_executed"] == 1
    assert snapshot["evidence_rejected_no_progress"] == 1
    assert snapshot["no_progress_streak"] == 1


# ---------------------------------------------------------------------------
# runtime 集成：新 evidence 超过旧 4 次 hard-stop / 旧 2 次 evidence budget
# ---------------------------------------------------------------------------


def _load_report(tmp_path):
    return json.loads(
        next((tmp_path / ".codecub" / "runs").glob("*/report.json")).read_text(
            encoding="utf-8"
        )
    )


def _make_agent(tmp_path, client):
    return MiniAgent(
        client,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        requires_workspace_change=True,
    )


def test_edit_decisions_continue_beyond_old_four_limit(tmp_path):
    """新 evidence 可以继续超过旧 4 次 edit decision、旧 2 次 evidence budget。"""

    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-phase26"
        last_completion_metadata = {}

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall("r0", "read_file", {"path": "target.py", "start": 1, "end": 10}),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "d1",
                            "submit_edit_decision",
                            {
                                "decision": "need_evidence",
                                "tool": "search",
                                "arguments": {"path": ".", "pattern": "old"},
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "d2",
                            "submit_edit_decision",
                            {
                                "decision": "need_evidence",
                                "tool": "read_file",
                                "arguments": {"path": "target.py", "start": 11, "end": 20},
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "d3",
                            "submit_edit_decision",
                            {
                                "decision": "need_evidence",
                                "tool": "search",
                                "arguments": {"path": ".", "pattern": "baseline"},
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "d4",
                            "submit_edit_decision",
                            {
                                "decision": "need_evidence",
                                "tool": "read_file",
                                "arguments": {"path": "target.py", "start": 21, "end": 30},
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "d5",
                            "submit_edit_decision",
                            {
                                "decision": "need_evidence",
                                "tool": "read_file",
                                "arguments": {"path": "target.py", "start": 31, "end": 40},
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "d6",
                            "submit_edit_decision",
                            {
                                "decision": "edit",
                                "tool": "patch_file",
                                "arguments": {
                                    "path": "target.py",
                                    "old_text": "old = 1",
                                    "new_text": "new = 1",
                                },
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "v1", "run_shell", {"command": "python -c \"print('ok')\""}
                        ),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice="auto"):
            self.requests.append((messages, tools, tool_choice))
            return self.responses.pop(0)

    (tmp_path / "target.py").write_text("old = 1\n", encoding="utf-8")
    client = NativeClient()
    agent = _make_agent(tmp_path, client)

    answer = agent.ask("Repair target.py")

    assert answer == "Done."
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "new = 1\n"
    report = _load_report(tmp_path)
    assert report["status"] == "completed"
    assert report["stop_reason"] == "final_answer_returned"
    # 超过旧 4 次 edit-decision hard-stop 与旧 2 次 evidence budget。
    assert report["planning"]["edit_decision_count"] >= 6
    assert report["planning"]["evidence_request_count"] >= 5
    assert report["edit_decision_watchdog"]["evidence_executed"] >= 5
    assert report["edit_decision_watchdog"]["evidence_rejected_no_progress"] == 0
    # 全程真实 progress：主 Watchdog 不应误判 stuck。
    assert report["watchdog"]["stuck_suspected_count"] == 0
    assert report["watchdog"]["stuck_confirmed_count"] == 0


def test_native_empty_final_is_rejected_not_completed(tmp_path):
    """Phase 2.6（Probe B/C 暴露）：native 空 final 不算成功完成，要求重试。"""
    calls = []

    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-empty-final"
        last_completion_metadata = {}

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(text=""),  # 空 final -> retry
                ModelResponse(text="Done."),  # 真实 final
            ]

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice="auto"):
            calls.append(1)
            self.requests.append((messages, tools, tool_choice))
            return self.responses.pop(0)

    client = NativeClient()
    agent = _make_agent(tmp_path, client)

    answer = agent.ask("Explain the repo")

    assert answer == "Done."
    assert len(calls) == 2
    # 第二次请求应包含“空 final”拒绝提示（user 消息），模型据此给出真实答案。
    assert any(
        "empty final" in str(m.get("content", ""))
        for m in client.requests[1][0]
        if m.get("role") == "user"
    )
    report = _load_report(tmp_path)
    assert report["status"] == "completed"
    assert report["final_answer"] == "Done."


def test_repeated_evidence_suspected_recovery_confirmed(tmp_path):
    """完全重复 evidence 且无 workspace change：拒绝 -> suspected -> recovery
    -> stuck_confirmed（不再 edit_decision_exhausted）。"""

    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-phase26-repeat"
        last_completion_metadata = {}

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall("r0", "read_file", {"path": "target.py", "start": 1, "end": 10}),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "d1",
                            "submit_edit_decision",
                            {
                                "decision": "need_evidence",
                                "tool": "read_file",
                                "arguments": {"path": "target.py", "start": 11, "end": 20},
                            },
                        ),
                    )
                ),
            ]
            for index in range(11):
                self.responses.append(
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                f"r{index}",
                                "submit_edit_decision",
                                {
                                    "decision": "need_evidence",
                                    "tool": "read_file",
                                    "arguments": {
                                        "path": "target.py",
                                        "start": 11,
                                        "end": 20,
                                    },
                                },
                            ),
                        )
                    )
                )

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice="auto"):
            self.requests.append((messages, tools, tool_choice))
            return self.responses.pop(0)

    (tmp_path / "target.py").write_text("old = 1\n", encoding="utf-8")
    client = NativeClient()
    agent = _make_agent(tmp_path, client)

    answer = agent.ask("Repair target.py")

    assert "appears stuck" in answer
    report = _load_report(tmp_path)
    assert report["status"] == "stopped"
    assert report["stop_reason"] == STOP_REASON_STUCK_CONFIRMED
    assert report["edit_decision_watchdog"]["evidence_rejected_no_progress"] >= 5
    assert report["edit_decision_watchdog"]["no_progress_streak"] >= 1
    assert report["watchdog"]["stuck_suspected_count"] >= 1
    assert report["watchdog"]["recovery_turn_count"] >= 1
    assert report["watchdog"]["stuck_confirmed_count"] == 1
