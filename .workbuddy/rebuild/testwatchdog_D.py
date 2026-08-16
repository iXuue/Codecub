"""Phase 1 — Long-Horizon Runtime 专项测试。

覆盖 CODECUB_PHASE1 实施任务的第十六节：Runtime Mode、Long Progress、
Identical / Alternating / Verification Loop、Progress Reset、Recovery、
Safety、Regression。Watchdog 单元测试直接驱动 ProgressWatchdog；
runtime 集成测试用 FakeModelClient 走完整 ask() 链路。
"""

import json

from codecub.runtime import (
    STOP_REASON_EMERGENCY_CAP_REACHED,
    STOP_REASON_STUCK_CONFIRMED,
    RUNTIME_MODE_EXPERIMENT,
    RUNTIME_MODE_INTERACTIVE,
)
from codecub.watchdog import (
    PATTERN_ALTERNATING_LOOP,
    PATTERN_IDENTICAL_LOOP,
    PATTERN_VERIFICATION_LOOP,
    ProgressWatchdog,
    WATCHDOG_STATE_NORMAL,
    WATCHDOG_STATE_RECOVERY,
    WATCHDOG_STATE_STUCK_CONFIRMED,
    WATCHDOG_STATE_STUCK_SUSPECTED,
)

from tests.test_pico import build_agent


def read_tool(path, start=1, end=5):
    args = json.dumps({"path": path, "start": start, "end": end}, sort_keys=True)
    return "<tool>" + json.dumps({"name": "read_file", "args": json.loads(args)}, sort_keys=True) + "</tool>"


def make_files(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_text("content\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Runtime Mode
# ---------------------------------------------------------------------------


def test_interactive_default_has_no_fixed_step_budget(tmp_path):
    agent = build_agent(tmp_path, [])
    assert agent.runtime_mode == RUNTIME_MODE_INTERACTIVE
    assert agent.max_steps is None
    assert agent.effective_step_budget is None


def test_experiment_mode_preserves_fixed_step_budget(tmp_path):
    agent = build_agent(
        tmp_path, [], runtime_mode=RUNTIME_MODE_EXPERIMENT, max_steps=24
    )
    assert agent.runtime_mode == RUNTIME_MODE_EXPERIMENT
    assert agent.effective_step_budget == 24


def test_experiment_mode_without_budget_falls_back_to_legacy_default(tmp_path):
    agent = build_agent(tmp_path, [], runtime_mode=RUNTIME_MODE_EXPERIMENT)
    assert agent.effective_step_budget == 80


def test_interactive_emergency_cap_effective(tmp_path):
    # 每步都有真实 progress（新文件），但达到 emergency cap 时兜底停止。
    cap = 8
    make_files(tmp_path, *["f%d.py" % i for i in range(cap + 1)])
    outputs = [read_tool("f%d.py" % i) for i in range(cap + 1)]
    outputs.append("<final>never</final>")
    agent = build_agent(tmp_path, outputs, emergency_cap=cap)

    answer = agent.ask("Scan many files")

    assert answer == (
        "Stopped after reaching the emergency step cap (%d) without a final answer." % cap
    )
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8")
    )
    assert report["stop_reason"] == STOP_REASON_EMERGENCY_CAP_REACHED
    assert report["tool_steps"] == cap
    assert report["emergency_cap"] == cap
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(event["event"] == "emergency_cap_reached" for event in trace)


# ---------------------------------------------------------------------------
# Long Progress
# ---------------------------------------------------------------------------


def test_long_progress_runs_beyond_old_small_budget(tmp_path):
    # 55 个产生真实 progress 的工具事件 + final，不应因“总步数”停止。
    make_files(tmp_path, *["m%d.py" % i for i in range(55)])
    outputs = [read_tool("m%d.py" % i) for i in range(55)]
    outputs.append("<final>Done after long run.</final>")
    agent = build_agent(tmp_path, outputs)

    answer = agent.ask("Long task")

    assert answer == "Done after long run."
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8")
    )
    assert report["status"] == "completed"
    assert report["tool_steps"] >= 50
    assert report["watchdog"]["state"] == WATCHDOG_STATE_NORMAL
    assert report["watchdog"]["stuck_suspected_count"] == 0


# ---------------------------------------------------------------------------
# Identical / Alternating / Verification Loop (watchdog 单元)
# ---------------------------------------------------------------------------


def test_identical_loop_enters_suspected():
    watchdog = ProgressWatchdog()
    decision = None
    for step in range(1, 6):
        decision = watchdog.record_tool_event(
            "read_file",
            {"path": "a.py", "start": 1, "end": 200},
            {"tool_status": "ok"},
            "content",
            step,
        )
    assert decision.suspected_now
    assert decision.stuck_pattern == PATTERN_IDENTICAL_LOOP
    assert watchdog.state == WATCHDOG_STATE_STUCK_SUSPECTED
    assert watchdog.stuck_suspected_count == 1


def test_alternating_loop_detected():
    watchdog = ProgressWatchdog()
    events = [
        ("list_files", {}),
        ("search", {"pattern": "foo"}),
        ("list_files", {}),
        ("search", {"pattern": "foo"}),
        ("list_files", {}),
        ("search", {"pattern": "foo"}),
    ]
    decision = None
    for step, (name, args) in enumerate(events, 1):
        decision = watchdog.record_tool_event(
            name, args, {"tool_status": "ok"}, "result", step
        )
    assert decision.suspected_now
    assert decision.stuck_pattern == PATTERN_ALTERNATING_LOOP


def test_verification_loop_detected():
    watchdog = ProgressWatchdog()
    decision = None
    for step in range(1, 5):
        decision = watchdog.record_tool_event(
            "run_shell",
            {"command": "pytest"},
            {"tool_status": "error", "tool_error_code": "tool_failed"},
            "exit_code: 1",
            step,
        )
    assert decision.suspected_now
    assert decision.stuck_pattern == PATTERN_VERIFICATION_LOOP


def test_workspace_change_resets_verification_loop():
    watchdog = ProgressWatchdog()
    for step in range(1, 4):
        watchdog.record_tool_event(
            "run_shell",
            {"command": "pytest"},
            {"tool_status": "error", "tool_error_code": "tool_failed"},
            "exit_code: 1",
            step,
        )
    decision = watchdog.record_tool_event(
        "patch_file",
        {"path": "a.py"},
        {"tool_status": "ok", "workspace_changed": True, "affected_paths": ["a.py"]},
        "ok",
        4,
    )
    assert decision.state == WATCHDOG_STATE_NORMAL
    decision = watchdog.record_tool_event(
        "run_shell",
        {"command": "pytest"},
        {"tool_status": "error", "tool_error_code": "tool_failed"},
        "exit_code: 1",
        5,
    )
    assert not decision.suspected_now


def test_semantic_read_loop_detected():
    watchdog = ProgressWatchdog()
    decision = None
    for step in range(1, 6):
        decision = watchdog.record_tool_event(
            "read_file",
            {"path": "a.py", "start": step, "end": step + 10},
            {"tool_status": "ok"},
            "content",
            step,
        )
    # 同文件高重叠区间的滑动读取：参数不完全相同，但区间高度重叠。
    assert decision.suspected_now
    assert decision.stuck_pattern in (PATTERN_IDENTICAL_LOOP, "semantic_read_loop")


# ---------------------------------------------------------------------------
# Progress Reset (watchdog 单元)
# ---------------------------------------------------------------------------


def _drive_to_suspected(watchdog, steps=5):
    for step in range(1, steps + 1):
        watchdog.record_tool_event(
            "read_file",
            {"path": "a.py", "start": 1, "end": 200},
            {"tool_status": "ok"},
            "content",
            step,
        )
    watchdog.begin_recovery(steps)
    assert watchdog.state == WATCHDOG_STATE_RECOVERY
    return watchdog


def test_new_file_evidence_recovers_normal():
    watchdog = _drive_to_suspected(ProgressWatchdog())
    decision = watchdog.record_tool_event(
        "read_file", {"path": "b.py", "start": 1, "end": 50}, {"tool_status": "ok"}, "b", 6
    )
    assert decision.recovered_now
    assert watchdog.state == WATCHDOG_STATE_NORMAL
    assert watchdog.recovery_success_count == 1


def test_new_symbol_evidence_recovers_normal():
    watchdog = _drive_to_suspected(ProgressWatchdog())
    decision = watchdog.record_tool_event(
        "symbol_search", {"query": "resolve", "path": "."}, {"tool_status": "ok"}, "symbols", 6
    )
    assert decision.recovered_now
    assert watchdog.state == WATCHDOG_STATE_NORMAL


def test_workspace_change_recovers_normal():
    watchdog = _drive_to_suspected(ProgressWatchdog())
    decision = watchdog.record_tool_event(
        "patch_file",
        {"path": "a.py"},
        {"tool_status": "ok", "workspace_changed": True, "affected_paths": ["a.py"]},
        "ok",
        6,
    )
    assert decision.recovered_now
    assert watchdog.state == WATCHDOG_STATE_NORMAL


def test_new_test_result_recovers_normal():
    watchdog = _drive_to_suspected(ProgressWatchdog())
    decision = watchdog.record_tool_event(
        "run_shell", {"command": "pytest"}, {"tool_status": "ok"}, "exit_code: 0", 6
    )
    assert decision.recovered_now
    assert watchdog.state == WATCHDOG_STATE_NORMAL


# ---------------------------------------------------------------------------
# Recovery (watchdog 单元 + runtime 集成)
# ---------------------------------------------------------------------------


def test_suspected_recovery_progress_normal_flow():
    watchdog = ProgressWatchdog()
    for step in range(1, 6):
        watchdog.record_tool_event(
            "read_file", {"path": "a.py", "start": 1, "end": 200}, {"tool_status": "ok"}, "a", step
        )
    watchdog.begin_recovery(5)
    decision = watchdog.record_tool_event(
        "read_file", {"path": "c.py", "start": 1, "end": 40}, {"tool_status": "ok"}, "c", 6
    )
    assert decision.recovered_now
    assert watchdog.state == WATCHDOG_STATE_NORMAL
    assert watchdog.recovery_success_count == 1
    assert watchdog.stuck_confirmed_count == 0


def test_suspected_recovery_no_progress_confirmed_flow():
    watchdog = ProgressWatchdog()
    for step in range(1, 6):
        watchdog.record_tool_event(
            "read_file", {"path": "a.py", "start": 1, "end": 200}, {"tool_status": "ok"}, "a", step
        )
    watchdog.begin_recovery(5)
    decision = None
    for step in range(6, 12):
        decision = watchdog.record_tool_event(
            "read_file", {"path": "a.py", "start": 1, "end": 200}, {"tool_status": "ok"}, "a", step
        )
    assert decision.confirmed_now
    assert watchdog.state == WATCHDOG_STATE_STUCK_CONFIRMED
    assert watchdog.stuck_confirmed_count == 1


def test_recovery_then_progress_recovers_normal_runtime(tmp_path):
    make_files(tmp_path, "a.py", "b.py")
    repeated = read_tool("a.py")
    new_file = read_tool("b.py")
    agent = build_agent(
        tmp_path,
        [repeated] * 5 + [new_file, "<final>Recovered.</final>"],
    )

    answer = agent.ask("Task that first looks stuck")

    assert answer == "Recovered."
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8")
    )
    assert report["status"] == "completed"
    assert report["watchdog"]["stuck_suspected_count"] == 1
    assert report["watchdog"]["recovery_turn_count"] == 1
    assert report["watchdog"]["recovery_success_count"] == 1
    assert report["watchdog"]["state"] == WATCHDOG_STATE_NORMAL
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    events = [event["event"] for event in trace]
    assert "stuck_suspected" in events
    assert "recovery_turn_started" in events
    assert "recovery_turn_finished" in events
    assert "progress_detected" in events


def test_continued_no_progress_confirms_and_stops_runtime(tmp_path):
    make_files(tmp_path, "a.py")
    repeated = read_tool("a.py")
    # 5 次触发 suspected；recovery 后继续 6 步相同无进展 -> confirmed。
    outputs = [repeated] * 11 + ["<final>never</final>"]
    agent = build_agent(tmp_path, outputs)

    answer = agent.ask("Stuck task")

    assert answer.startswith("Agent paused because it appears stuck.")
    assert "Current blocker" in answer
    assert "Last useful progress" in answer
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8")
    )
    assert report["status"] == "stopped"
    assert report["stop_reason"] == STOP_REASON_STUCK_CONFIRMED
    assert report["watchdog"]["stuck_suspected_count"] == 1
    assert report["watchdog"]["recovery_turn_count"] == 1
    assert report["watchdog"]["stuck_confirmed_count"] == 1
    assert report["watchdog"]["state"] == WATCHDOG_STATE_STUCK_CONFIRMED
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    events = [event["event"] for event in trace]
    assert "stuck_suspected" in events
    assert "stuck_confirmed" in events
    assert "recovery_turn_started" in events


def test_experiment_mode_confirmed_uses_stop_reason(tmp_path):
    make_files(tmp_path, "a.py")
    repeated = read_tool("a.py")
    outputs = [repeated] * 11 + ["<final>never</final>"]
    agent = build_agent(
        tmp_path,
        outputs,
        runtime_mode=RUNTIME_MODE_EXPERIMENT,
        max_steps=24,
    )

    answer = agent.ask("Stuck experiment task")

    assert "appeared stuck" in answer
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8")
    )
    assert report["stop_reason"] == STOP_REASON_STUCK_CONFIRMED
    assert report["runtime_mode"] == RUNTIME_MODE_EXPERIMENT
    assert report["effective_step_budget"] == 24


def test_experiment_mode_still_respects_step_budget(tmp_path):
    # 每步都产生 progress（不同文件），不触发 stuck；只验证 24 步固定预算。
    make_files(tmp_path, *["b%d.py" % i for i in range(30)])
    outputs = [read_tool("b%d.py" % i) for i in range(30)]
    outputs.append("<final>never</final>")
    agent = build_agent(
        tmp_path,
        outputs,
        runtime_mode=RUNTIME_MODE_EXPERIMENT,
        max_steps=24,
    )

    answer = agent.ask("Budgeted task")

    assert "step limit" in answer
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8")
    )
    assert report["stop_reason"] == "step_limit_reached"
    assert report["tool_steps"] == 24


# ---------------------------------------------------------------------------
# Safety 不因 unlimited 弱化
# ---------------------------------------------------------------------------


def test_interactive_unlimited_still_blocks_path_escape(tmp_path):
    agent = build_agent(tmp_path, [], runtime_mode=RUNTIME_MODE_INTERACTIVE)
    result = agent.run_tool("read_file", {"path": "../outside.py"})
    assert "path escapes workspace" in result
    assert agent._last_tool_result_metadata["security_event_type"] == "path_escape"


def test_interactive_unlimited_still_executes_approval(tmp_path):
    decisions = []

    def approval_handler(name, args, runtime):
        del args, runtime
        decisions.append(name)
        return False

    agent = build_agent(
        tmp_path,
        [],
        runtime_mode=RUNTIME_MODE_INTERACTIVE,
        approval_policy="ask",
        approval_handler=approval_handler,
    )
    (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
    result = agent.run_tool("write_file", {"path": "f.py", "content": "x = 2\n"})
    assert result == "error: approval denied for write_file"
    assert decisions == ["write_file"]


def test_interactive_unlimited_read_only_still_blocks_mutation(tmp_path):
    agent = build_agent(
        tmp_path, [], runtime_mode=RUNTIME_MODE_INTERACTIVE, read_only=True
    )
    result = agent.run_tool("write_file", {"path": "f.py", "content": "x = 1\n"})
    assert "approval denied" in result


def test_interactive_unlimited_ambiguous_patch_still_rejected(tmp_path):
    target = tmp_path / "f.py"
    target.write_text("x = 1\nx = 1\n", encoding="utf-8")
    agent = build_agent(tmp_path, [], runtime_mode=RUNTIME_MODE_INTERACTIVE)
    result = agent.run_tool(
        "patch_file",
        {"path": "f.py", "old_text": "x = 1", "new_text": "y = 1"},
    )
    assert "must occur exactly once" in result


# ---------------------------------------------------------------------------
# Regression: FakeModel harness 与旧链路
# ---------------------------------------------------------------------------


def test_fake_model_harness_still_completes_short_run(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":5}}</tool>',
            "<final>Ok.</final>",
        ],
    )
    assert agent.ask("Explain the repo") == "Ok."
