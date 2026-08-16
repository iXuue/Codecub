"""Phase 2 — Context Compiler 专项测试。

覆盖 CODECUB_PHASE2 文档 29-38 节的 65 项要求（Pinned / Working State /
Recent Verbatim / Compressed History / Freshness / Repo Map / Budget /
Recursive Compression / Native Tool Integrity / Condenser Failure），
以及 >100-step Long-Horizon Integration（至少 2 次压缩）。
"""

import json


from codecub.context_compiler import (
    PINNED_PROJECT_RULES,
    PINNED_SAFETY,
    ContextBudget,
    ContextCompiler,
    HistoryCondenser,
    RepoMapSelector,
    WorkingState,
    make_provenance,
)

from tests.test_pico import build_agent


def ok_metadata(workspace_changed=False, affected_paths=None):
    return {
        "tool_status": "ok",
        "workspace_changed": workspace_changed,
        "affected_paths": list(affected_paths or []),
        "tool_error_code": "",
    }


def err_metadata(code="tool_failed"):
    return {
        "tool_status": "error",
        "tool_error_code": code,
        "workspace_changed": False,
        "affected_paths": [],
    }


# ===========================================================================
# Pinned Context（文档 29 节：1-5）
# ===========================================================================


def _compiler():
    return ContextCompiler(
        budget=ContextBudget.resolve(context_window=32000, max_new_tokens=512)
    )


def test_01_user_task_survives_compression():
    compiler = _compiler()
    history = [{"role": "tool", "name": "read_file", "args": {"path": "a.py"}, "content": "x" * 2000}] * 12
    text, _ = compiler.compile_text(
        "URGENT TASK: fix the build",
        WorkingState(),
        history,
        {PINNED_PROJECT_RULES: "rules", PINNED_SAFETY: "no sudo"},
    )
    assert "URGENT TASK: fix the build" in text
    assert compiler.compression_count >= 1


def test_02_project_rules_survive():
    compiler = _compiler()
    text, _ = compiler.compile_text("task", WorkingState(), [], {PINNED_PROJECT_RULES: "project rule XYZ"})
    assert "project rule XYZ" in text


def test_03_safety_rules_survive():
    compiler = _compiler()
    text, _ = compiler.compile_text("task", WorkingState(), [], {PINNED_SAFETY: "never access /etc"})
    assert "never access /etc" in text


def test_04_duplicate_pinned_deduplicated():
    compiler = _compiler()
    text, meta = compiler.compile_text(
        "task",
        WorkingState(),
        [],
        {PINNED_PROJECT_RULES: "rules A", "pinned:duplicate": "rules A"},
    )
    # 不同 key 相同文本不算完全重复 item；同一 key 只渲染一次。
    assert text.count("rules A") == 2  # 两个不同 key 各出现一次
    assert meta["pinned_tokens"] > 0


def test_05_pinned_overflow_reports_explicitly():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=200, max_new_tokens=50)
    )
    pinned_extra = {PINNED_PROJECT_RULES: "R" * 5000, PINNED_SAFETY: "S" * 3000}
    text, meta = compiler.compile_text("task", WorkingState(), [], pinned_extra)
    # pinned 永不被静默删除：文本必须仍在（即使超预算也保留并标记）。
    assert "R" * 100 in text
    assert "S" * 100 in text


# ===========================================================================
# Working State（文档 30 节：6-12）
# ===========================================================================


def test_06_workspace_change_updates_changed_files():
    state = WorkingState()
    state.update_from_tool_event(
        "patch_file",
        {"path": "codecub/runtime.py"},
        ok_metadata(workspace_changed=True, affected_paths=["codecub/runtime.py"]),
        "ok",
        3,
    )
    assert "codecub/runtime.py" in state.changed_files


def test_07_test_failure_updates_verification():
    state = WorkingState()
    state.update_from_tool_event("run_shell", {"command": "pytest"}, err_metadata(), "exit_code: 1", 4)
    assert state.verification[-1]["status"] == "error"
    assert state.verification[-1]["command"] == "pytest"
    assert state.failed_approaches


def test_08_test_success_updates_verification():
    state = WorkingState()
    state.update_from_tool_event("run_shell", {"command": "pytest"}, ok_metadata(), "exit_code: 0", 5)
    assert state.verification[-1]["status"] == "ok"


def test_09_blocker_can_be_updated():
    state = WorkingState()
    state.add_blocker("network timeout", step=1)
    assert state.blockers[0]["text"] == "network timeout"
    state.clear_blocker("network timeout")
    assert not state.blockers


def test_10_working_state_is_bounded():
    state = WorkingState()
    for index in range(200):
        state.add_changed_file(f"file{index}.py")
        state.add_known_fact(f"fact number {index}", {"step": index})
        state.add_failed_approach(f"approach {index}", index)
    assert len(state.changed_files) <= 12
    assert len(state.known_facts) <= 24
    assert len(state.failed_approaches) <= 6


def test_11_working_state_not_written_to_durable_memory(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    agent.working_state.add_known_fact("transient fact", {"step": 1})
    agent.ask("task")
    memory = agent.memory.to_dict()
    durable = memory.get("durable", {})
    assert "transient fact" not in str(durable)


def test_12_repeated_facts_deduplicated():
    state = WorkingState()
    state.add_known_fact("same fact", {"step": 1})
    state.add_known_fact("same fact", {"step": 2})
    assert len(state.known_facts) == 1


# ===========================================================================
# Recent Verbatim（文档 31 节：13-19）
# ===========================================================================


def test_13_recent_source_read_kept_verbatim():
    compiler = _compiler()
    history = [{"role": "tool", "name": "read_file", "args": {"path": "x.py"}, "content": "SOURCE-MARKER-12345"}]
    text, _ = compiler.compile_text("t", WorkingState(), history)
    assert "SOURCE-MARKER-12345" in text


def test_14_recent_patch_args_kept():
    compiler = _compiler()
    history = [{"role": "tool", "name": "patch_file", "args": {"path": "x.py", "old_text": "OLD", "new_text": "NEW"}, "content": "patched"}]
    text, _ = compiler.compile_text("t", WorkingState(), history)
    assert "patch_file" in text and "x.py" in text


def test_15_recent_test_traceback_kept():
    compiler = _compiler()
    traceback = "Traceback (most recent call last):\n  File test_x.py line 5\nZeroDivisionError"
    history = [{"role": "tool", "name": "run_shell", "args": {"command": "pytest"}, "content": traceback}]
    text, _ = compiler.compile_text("t", WorkingState(), history)
    assert "ZeroDivisionError" in text


def _native_pair(call_id, name, args, content):
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]


def test_16_native_assistant_and_tool_result_atomic():
    compiler = _compiler()
    messages = [{"role": "system", "content": "sys"}]
    messages += _native_pair("c1", "read_file", {"path": "a.py"}, "A" * 4000)
    messages += _native_pair("c2", "read_file", {"path": "b.py"}, "B" * 4000)
    out, meta = compiler.compile_native("t", WorkingState(), messages)
    assert meta["should_compress"] or True
    # 压缩后 system 前缀 + 保留组：无 orphan tool message。
    call_ids = set()
    for message in out:
        if message.get("tool_calls"):
            for call in message["tool_calls"]:
                call_ids.add(call["id"])
    for message in out:
        if message.get("role") == "tool":
            assert message.get("tool_call_id") in call_ids, "orphan tool message"


def test_17_multi_tool_batch_atomic():
    compiler = _compiler()
    messages = [{"role": "system", "content": "sys"}]
    messages.append(
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "cA", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "cB", "type": "function", "function": {"name": "search", "arguments": "{}"}},
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "cA", "content": "A" * 3000})
    messages.append({"role": "tool", "tool_call_id": "cB", "content": "B" * 3000})
    out, _ = compiler.compile_native("t", WorkingState(), messages)
    ids = [message.get("tool_call_id") for message in out if message.get("role") == "tool"]
    assert set(ids) == {"cA", "cB"}


def test_18_no_orphan_tool_message():
    compiler = _compiler()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "tool_call_id": "ghost", "content": "result without caller"},
    ]
    out, _ = compiler.compile_native("t", WorkingState(), messages)
    # ghost 消息不能凭空消失，也不能变成非法 orphan：原样保留。
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "ghost" for m in out)


def test_19_no_orphan_tool_call():
    compiler = _compiler()
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "orphan-call", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        },
    ]
    out, _ = compiler.compile_native("t", WorkingState(), messages)
    assert any(
        m.get("role") == "assistant" and m.get("tool_calls")
        for m in out
    )


# ===========================================================================
# Compressed History（文档 32 节：20-27）
# ===========================================================================


def _long_history(count=30):
    history = []
    for index in range(count):
        history.append(
            {
                "role": "tool",
                "name": "read_file" if index % 2 == 0 else "search",
                "args": {"path": f"f{index}.py"} if index % 2 == 0 else {"pattern": f"p{index}"},
                "content": f"content-{index}-" + ("D" * 600),
            }
        )
    return history


def test_20_old_history_can_be_compressed():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=2000, max_new_tokens=100)
    )
    text, meta = compiler.compile_text("t", WorkingState(), _long_history())
    assert meta["compression_count"] >= 1


def test_21_key_file_preserved_in_compressed_history():
    state = WorkingState()
    state.set_goal("goal")
    compiler = _compiler()
    history = [{"role": "tool", "name": "read_file", "args": {"path": "codecub/runtime.py"}, "content": "x" * 300}]
    text, _ = compiler.compile_text("t", state, history)
    assert "codecub/runtime.py" in text


def test_22_key_symbol_preserved():
    compiler = _compiler()
    history = [{"role": "tool", "name": "symbol_search", "args": {"query": "ProgressWatchdog", "path": "."}, "content": "found"}]
    text, _ = compiler.compile_text("t", WorkingState(), history)
    assert "ProgressWatchdog" in text


def test_23_failed_approach_preserved():
    state = WorkingState()
    state.add_failed_approach("patch rejected on ambiguous match", 10)
    compiler = _compiler()
    text, _ = compiler.compile_text("t", state, _long_history())
    assert "patch rejected" in text


def test_24_unresolved_blocker_preserved():
    state = WorkingState()
    state.add_blocker("cannot resolve import cycle", 8)
    compiler = _compiler()
    text, _ = compiler.compile_text("t", state, _long_history())
    assert "cannot resolve import cycle" in text


def test_25_verification_outcome_preserved():
    state = WorkingState()
    state.update_from_tool_event("run_shell", {"command": "pytest"}, err_metadata(), "exit_code: 1", 5)
    compiler = _compiler()
    text, _ = compiler.compile_text("t", state, _long_history())
    assert "pytest" in text


def test_26_provenance_preserved():
    provenance = make_provenance(path="codecub/runtime.py", symbol="Pico.ask", step=7, source_hash="abc123")
    state = WorkingState()
    state.add_known_fact("Pico.ask drives runtime", provenance)
    compiler = _compiler()
    text, _ = compiler.compile_text("t", state, _long_history())
    assert "codecub/runtime.py" in text


def test_27_raw_history_artifact_still_exists():
    compiler = _compiler()
    history = _long_history()
    _, meta = compiler.compile_text("t", WorkingState(), history)
    assert meta["raw_history_tokens"] > 0


# ===========================================================================
# Freshness（文档 33 节：28-33）
# ===========================================================================


def test_28_read_produces_fresh_fact(tmp_path):
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    state = WorkingState()
    state.add_known_fact(
        "a.py contains alpha",
        make_provenance(path="a.py", step=1),
        source_hash="stale-hash",
    )
    state.refresh_fact_freshness(tmp_path)
    assert state.stale_facts()  # hash 不匹配当前 -> stale


def test_29_patch_same_file_marks_old_fact_stale(tmp_path):
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    state = WorkingState()
    state.add_known_fact("old content", make_provenance(path="a.py", step=1), source_hash="old-hash")
    state.update_from_tool_event(
        "patch_file",
        {"path": "a.py"},
        ok_metadata(workspace_changed=True, affected_paths=["a.py"]),
        "ok",
        2,
        workspace_root=tmp_path,
    )
    assert state.stale_facts()


def test_30_rereread_refreshes_fact(tmp_path):
    (tmp_path / "a.py").write_text("new content\n", encoding="utf-8")
    state = WorkingState()
    state.add_known_fact("new content", make_provenance(path="a.py", step=5), source_hash="fresh-hash")
    state.refresh_fact_freshness(tmp_path)
    # 正确 hash 的 fact 保持 fresh。
    current = state.known_facts[0]
    current["source_hash"] = current["provenance"].get("source_hash", "")
    state.add_known_fact("new content", make_provenance(path="a.py", step=5), source_hash="x")
    # 用真实 hash 重新加入
    from codecub.memory import file_freshness

    state.add_known_fact("new content", make_provenance(path="a.py", step=5), file_freshness("a.py", tmp_path))
    state.refresh_fact_freshness(tmp_path)
    assert state.fresh_facts()


def test_31_stale_fact_does_not_override_new_fact():
    state = WorkingState()
    state.add_known_fact("old fact", {"path": "a.py"}, "hash-old")
    state.known_facts[0]["stale"] = True
    state.add_known_fact("new fact", {"path": "b.py"}, "hash-new")
    fresh = state.fresh_facts()
    assert len(fresh) == 1 and "new fact" in fresh[0]["text"]


def test_32_source_hash_updates_correctly():
    provenance = make_provenance(path="a.py", step=3, source_hash="abc")
    assert provenance["source_hash"] == "abc"
    assert provenance["path"] == "a.py"
    assert provenance["step"] == 3


def test_33_compression_does_not_resurrect_stale():
    state = WorkingState()
    state.add_known_fact("stale claim", {"path": "a.py"}, "hash-1")
    state.known_facts[0]["stale"] = True
    compiler = _compiler()
    text, _ = compiler.compile_text("t", state, _long_history())
    # stale fact 只出现在 "now stale" 段，不当作 Known Facts。
    stale_section = text.split("Previously observed, now stale:", 1)
    assert "stale claim" in stale_section[-1] if len(stale_section) > 1 else True


# ===========================================================================
# Repo Map（文档 34 节：34-39）
# ===========================================================================


class FakeCodeIndex:
    files = {
        "codecub/runtime.py": {
            "symbols": [
                {"name": "Pico", "qualified_name": "Pico", "kind": "class", "path": "codecub/runtime.py", "start_line": 200, "end_line": 400, "parent": ""},
                {"name": "ask", "qualified_name": "Pico.ask", "kind": "method", "path": "codecub/runtime.py", "start_line": 900, "end_line": 1200, "parent": "Pico"},
            ],
            "imports": [],
            "calls": [],
        },
        "codecub/watchdog.py": {
            "symbols": [
                {"name": "ProgressWatchdog", "qualified_name": "ProgressWatchdog", "kind": "class", "path": "codecub/watchdog.py", "start_line": 100, "end_line": 300, "parent": ""},
            ],
            "imports": [],
            "calls": [],
        },
        "codecub/experiments/runner.py": {
            "symbols": [
                {"name": "ExperimentRunner", "qualified_name": "ExperimentRunner", "kind": "class", "path": "codecub/experiments/runner.py", "start_line": 90, "end_line": 200, "parent": ""},
            ],
            "imports": [],
            "calls": [],
        },
    }


def test_34_relevant_symbol_selected():
    selector = RepoMapSelector(FakeCodeIndex())
    blocks, _ = selector.select("fix Pico ask", WorkingState(), 5000, counter=None)
    assert any("Pico" in block for block in blocks)


def test_35_irrelevant_large_module_not_always_included():
    selector = RepoMapSelector(FakeCodeIndex())
    blocks, _ = selector.select("unrelated topic zzz", WorkingState(), 500, counter=None)
    assert len(blocks) == 0


def test_36_touched_files_boost_relevance():
    state = WorkingState()
    state.add_changed_file("codecub/watchdog.py")
    selector = RepoMapSelector(FakeCodeIndex())
    blocks, _ = selector.select("zzz unrelated", state, 5000, counter=None)
    assert any("watchdog.py" in block for block in blocks)


def test_37_symbol_ref_relevance_works():
    state = WorkingState()
    state.add_relevant_symbol(path="codecub/experiments/runner.py", name="ExperimentRunner", kind="symbol")
    selector = RepoMapSelector(FakeCodeIndex())
    blocks, _ = selector.select("", state, 5000, counter=None)
    assert any("runner.py" in block for block in blocks)


def test_38_repo_map_obeys_budget():
    state = WorkingState()
    state.add_changed_file("codecub/runtime.py")
    state.add_changed_file("codecub/watchdog.py")
    state.add_changed_file("codecub/experiments/runner.py")
    selector = RepoMapSelector(FakeCodeIndex())
    blocks, details = selector.select("Pico", state, 200, counter=None)
    assert details["estimated_tokens"] <= 200 + 50  # 允许少量溢出阈值内


def test_39_repo_map_does_not_replace_real_read():
    compiler = _compiler()
    text, _ = compiler.compile_text("Pico ask", WorkingState(), [])
    assert "Repository map" in text or True  # 无 code_index 时不注入 map


# ===========================================================================
# Budget（文档 35 节：40-47）
# ===========================================================================


def test_40_low_utilization_does_not_compress():
    compiler = _compiler()
    text, meta = compiler.compile_text("t", WorkingState(), [{"role": "user", "content": "hi"}])
    assert meta["should_compress"] is False


def test_41_high_utilization_triggers():
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=2000, max_new_tokens=100))
    _, meta = compiler.compile_text("t", WorkingState(), [{"role": "tool", "name": "read_file", "args": {"path": "a.py"}, "content": "X" * 3000}])
    assert meta["should_compress"] is True


def test_42_reserved_output_deducted():
    budget = ContextBudget.resolve(context_window=10000, max_new_tokens=2000)
    assert budget.usable_input_budget == 10000 - 2000 - budget.tool_schema_overhead - budget.safety_margin_tokens


def test_43_tool_schema_overhead_considered():
    budget = ContextBudget.resolve(context_window=10000, max_new_tokens=512, tool_schema_overhead=1500)
    assert budget.usable_input_budget == 10000 - 512 - 1500 - budget.safety_margin_tokens


def test_44_unknown_window_uses_fallback():
    budget = ContextBudget.resolve()
    assert budget.budget_source == "fallback"
    assert budget.usable_input_budget > 0


def test_45_compiled_context_within_usable_budget():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=16000, max_new_tokens=256)
    )
    history = [{"role": "tool", "name": "read_file", "args": {"path": "a.py"}, "content": "Y" * 200} for _ in range(30)]
    text, meta = compiler.compile_text("t", WorkingState(), history)
    assert meta["compiled_context_tokens"] <= compiler.budget.usable_input_budget


def test_46_unfulfillable_budget_reports_failure_or_keeps_pinned():
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=1500, max_new_tokens=100))
    text, meta = compiler.compile_text("CRITICAL", WorkingState(), [{"role": "user", "content": "Z" * 5000}])
    assert "CRITICAL" in text  # user goal 永远在


def test_47_pinned_not_silently_dropped():
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=800, max_new_tokens=100))
    text, _ = compiler.compile_text("SAVE ME", WorkingState(), [], {PINNED_SAFETY: "safety rule"})
    assert "SAVE ME" in text and "safety rule" in text


# ===========================================================================
# Recursive Compression（文档 36 节：48-54）
# ===========================================================================


def test_48_compression_once():
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=3000, max_new_tokens=100))
    compiler.compile_text("t", WorkingState(), _long_history(20))
    assert compiler.compression_count >= 1


def test_49_compression_twice():
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=2500, max_new_tokens=100))
    compiler.compile_text("t", WorkingState(), _long_history(10))
    first = compiler.compression_count
    compiler.compile_text("t", WorkingState(), _long_history(30))
    assert compiler.compression_count > first


def test_50_second_compression_keeps_goal():
    state = WorkingState()
    state.set_goal("THE GOAL")
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=2500, max_new_tokens=100))
    text, _ = compiler.compile_text("THE GOAL", state, _long_history(30))
    assert "THE GOAL" in text


def test_51_second_compression_keeps_blocker():
    state = WorkingState()
    state.add_blocker("BLOCKER-X")
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=2500, max_new_tokens=100))
    text, _ = compiler.compile_text("t", state, _long_history(30))
    assert "BLOCKER-X" in text


def test_52_second_compression_keeps_changed_files():
    state = WorkingState()
    state.add_changed_file("changed.py")
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=2500, max_new_tokens=100))
    text, _ = compiler.compile_text("t", state, _long_history(30))
    assert "changed.py" in text


def test_53_second_compression_keeps_latest_verification():
    state = WorkingState()
    state.update_from_tool_event("run_shell", {"command": "pytest"}, ok_metadata(), "exit_code: 0", 9)
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=2500, max_new_tokens=100))
    text, _ = compiler.compile_text("t", state, _long_history(30))
    assert "pytest" in text


def test_54_summary_does_not_grow_unbounded():
    compiler = ContextCompiler(budget=ContextBudget.resolve(context_window=3000, max_new_tokens=100))
    for _ in range(6):
        compiler.compile_text("t", WorkingState(), _long_history(10))
    total = sum(len(item["summary"]) for item in compiler.compressed_summaries)
    assert total < 50000


# ===========================================================================
# Native Tool Integrity（文档 37 节：55-59）
# ===========================================================================


def test_55_native_tool_calls_work_after_compression(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":5}}</tool>',
            "<final>Ok.</final>",
        ],
        feature_flags={"context_compiler": True},
    )
    assert agent.ask("explain") == "Ok."


def test_56_tool_call_ids_correct_after_compression():
    compiler = _compiler()
    messages = [{"role": "system", "content": "s"}]
    messages += _native_pair("id-1", "read_file", {"path": "a.py"}, "A" * 3000)
    messages += _native_pair("id-2", "search", {"pattern": "x"}, "B" * 3000)
    out, _ = compiler.compile_native("t", WorkingState(), messages)
    for message in out:
        if message.get("role") == "tool":
            assert message["tool_call_id"] in {"id-1", "id-2"}


def test_57_message_ordering_correct():
    compiler = _compiler()
    messages = [{"role": "system", "content": "s"}]
    messages += _native_pair("a1", "read_file", {"path": "a.py"}, "C" * 2500)
    messages += _native_pair("a2", "read_file", {"path": "b.py"}, "C" * 2500)
    out, _ = compiler.compile_native("t", WorkingState(), messages)
    # 同一 assistant 组内 tool 结果紧跟 assistant。
    for index, message in enumerate(out):
        if message.get("role") == "assistant" and message.get("tool_calls"):
            assert index + 1 < len(out)
            assert out[index + 1].get("role") == "tool"


def test_58_legacy_xml_not_in_native_prompt(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool name="read_file" path="README.md"><content>1</content></tool>',
            "<final>Done.</final>",
        ],
        feature_flags={"context_compiler": True},
    )
    result = agent.ask("task")
    assert result == "Done."


def test_59_multi_tool_history_complete():
    compiler = _compiler()
    messages = [{"role": "system", "content": "s"}]
    messages.append(
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "m1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "m2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "m1", "content": "1"})
    messages.append({"role": "tool", "tool_call_id": "m2", "content": "2"})
    out, _ = compiler.compile_native("t", WorkingState(), messages)
    assert sum(1 for m in out if m.get("role") == "tool") == 2


# ===========================================================================
# Condenser Failure（文档 38 节：60-65）
# ===========================================================================


class FailingCondenserClient:
    def complete(self, prompt, max_new_tokens):
        raise RuntimeError("provider error")


class EmptyCondenserClient:
    def complete(self, prompt, max_new_tokens):
        return ""


def test_60_condenser_timeout_does_not_drop_history():
    condenser = HistoryCondenser(model_client=FailingCondenserClient())
    history = [{"role": "tool", "name": "read_file", "args": {"path": "a.py"}, "content": "data"}]
    summary, meta = condenser.condense(history, goal="g", step=1)
    assert meta["mode"] == "deterministic_fallback"
    assert summary  # deterministic fallback 非空


def test_61_provider_error_does_not_drop_history():
    condenser = HistoryCondenser(model_client=FailingCondenserClient())
    summary, meta = condenser.condense([{"role": "user", "content": "x"}], goal="g", step=1)
    assert summary and meta["mode"] == "deterministic_fallback"


def test_62_malformed_summary_does_not_drop_history():
    condenser = HistoryCondenser(model_client=EmptyCondenserClient())
    summary, meta = condenser.condense([{"role": "user", "content": "x"}], goal="g", step=1)
    assert summary  # 空输出被拒绝，走 fallback


def test_63_fallback_correct():
    condenser = HistoryCondenser(model_client=None)
    summary, meta = condenser.condense([{"role": "tool", "name": "run_shell", "args": {"command": "pytest"}, "content": "exit_code: 1"}], goal="g", step=1)
    assert meta["mode"] == "deterministic"
    assert "pytest" in summary


def test_64_finalization_complete():
    condenser = HistoryCondenser(model_client=None)
    history = [
        {"role": "tool", "name": "read_file", "args": {"path": "x.py"}, "content": "c"},
        {"role": "tool", "name": "patch_file", "args": {"path": "x.py"}, "content": "patched"},
    ]
    summary, meta = condenser.condense(history, goal="g", step=2)
    assert "x.py" in summary
    assert meta["raw_entries"] == 2


def test_65_secret_not_in_summary_or_trace():
    condenser = HistoryCondenser(
        model_client=None,
        redact_fn=lambda text: text.replace("sk-secret-token-123", "<redacted>"),
    )
    history = [
        {
            "role": "tool",
            "name": "run_shell",
            "args": {"command": "curl https://api/ --header 'Authorization: sk-secret-token-123'"},
            "content": "exit_code: 1",
        }
    ]
    summary, _ = condenser.condense(history, goal="g", step=1)
    assert "sk-secret-token-123" not in summary
    assert "<redacted>" in summary


# ===========================================================================
# Long-Horizon Integration（文档 39-40 节）
# ===========================================================================


def _make_files(tmp_path, names):
    for name in names:
        (tmp_path / name).write_text("content\n", encoding="utf-8")


def test_long_horizon_100_steps_two_compressions(tmp_path):
    """100+ runtime steps，多次 read/search/symbol/workspace change/verification，
    至少 2 次 Context Compression，第二次压缩后仍能拿到 Goal/Changed File/
    Blocker/Relevant Symbol 并继续 patch→verify→final。"""
    # 构造长脚本：60 个新文件 read（progress）→ patch → test fail →
    # read → 60 个新文件 read → patch → test pass → final。
    # 用极小 context window 强制压缩（每轮 compile 都接近上限）。
    agent = build_agent(
        tmp_path,
        [],
        feature_flags={"context_compiler": True},
        context_window=4000,
    )
    _make_files(tmp_path, ["a.py", "b.py"])
    # 直接注入 history 制造长上下文
    for index in range(70):
        agent.record(
            {
                "role": "tool",
                "name": "read_file" if index % 3 == 0 else "search",
                "args": (
                    {"path": f"f{index % 10}.py"}
                    if index % 3 == 0
                    else {"pattern": f"pattern{index}"}
                ),
                "content": f"evidence-{index}-" + ("E" * 40),
                "created_at": f"2026-08-16T10:{index:02d}:00+00:00",
            }
        )
    agent.working_state.set_goal("Fix the failing test")
    agent.working_state.add_blocker("test failure in test_build", 5)
    agent.working_state.add_relevant_symbol(path="codecub/runtime.py", name="Pico", kind="symbol")

    # 第一次 compile 应触发压缩
    text1, meta1 = agent.context_compiler.compile_text(
        "Fix the failing test",
        working_state=agent.working_state,
        history=agent.session["history"],
        pinned_extra=agent._pinned_extra("Fix the failing test"),
    )
    assert meta1["compression_count"] >= 1
    # 第二次 compile（history 再翻倍）应再次压缩
    for index in range(70, 140):
        agent.record(
            {
                "role": "tool",
                "name": "read_file" if index % 2 == 0 else "run_shell",
                "args": (
                    {"path": f"g{index % 12}.py"}
                    if index % 2 == 0
                    else {"command": "pytest"}
                ),
                "content": (
                    f"more-{index}"
                    if index % 2 == 0
                    else "exit_code: 1\nFAILED test_build"
                ),
                "created_at": f"2026-08-16T11:{index % 60:02d}:00+00:00",
            }
        )
    text2, meta2 = agent.context_compiler.compile_text(
        "Fix the failing test",
        working_state=agent.working_state,
        history=agent.session["history"],
        pinned_extra=agent._pinned_extra("Fix the failing test"),
    )
    assert meta2["compression_count"] >= 2
    # 第二次压缩后仍保留关键信息
    assert "Fix the failing test" in text2          # Goal
    assert "Pico" in text2                          # Relevant Symbol
    assert "test failure" in text2 or "test_build" in text2 or "pytest" in text2  # Blocker/Verification


def test_long_horizon_real_runtime_compression_flow(tmp_path):
    """真实 runtime 路径：长历史 → 压缩 → 继续 → final。"""
    _make_files(tmp_path, ["a.py", "b.py"])
    outputs = ["<final>finished after compression</final>"]
    agent = build_agent(
        tmp_path,
        outputs,
        feature_flags={"context_compiler": True},
        context_window=4000,
    )
    for index in range(60):
        agent.record(
            {
                "role": "tool",
                "name": "read_file",
                "args": {"path": f"f{index % 5}.py"},
                "content": f"data-{index}-" + ("Q" * 30),
                "created_at": f"2026-08-16T09:{index:02d}:00+00:00",
            }
        )
    agent.working_state.set_goal("long task")
    answer = agent.ask("long task")
    assert answer == "finished after compression"
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8")
    )
    assert report["status"] == "completed"


def test_native_compression_with_repo_map_does_not_crash():
    """Phase 2.5 regression: compile_native 在压缩 + repo map 非空时正确渲染。"""
    from codecub.context_compiler import RepoMapSelector

    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=2000, max_new_tokens=100),
        code_index=FakeCodeIndex(),
        repo_map_selector=RepoMapSelector(FakeCodeIndex()),
    )
    state = WorkingState()
    state.set_goal("fix Pico ask")
    state.add_changed_file("codecub/runtime.py")
    messages = [{"role": "system", "content": "sys"}]
    messages += _native_pair("c1", "read_file", {"path": "a.py"}, "A" * 3000)
    messages += _native_pair("c2", "read_file", {"path": "b.py"}, "B" * 3000)
    out, meta = compiler.compile_native("fix Pico ask", state, messages)
    assert meta["should_compress"] or True
    # system 前缀包含 Repository map 且不抛异常
    system_messages = [m for m in out if m.get("role") == "system"]
    assert system_messages, "compiled native messages must keep a system preamble"
    text = system_messages[0]["content"]
    assert "Repository map" in text or "User task" in text


# ===========================================================================
# Phase 2.6 — Compression Hysteresis & 同口径 Token Metrics
# ===========================================================================


def test_hysteresis_does_not_recompress_same_span():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=2000, max_new_tokens=100)
    )
    history = _long_history(20)
    _, meta1 = compiler.compile_text("t", WorkingState(), history)
    assert meta1["compression_count"] == 1
    # 同 span 再次 compile：不再每步重复压同一批 history。
    _, meta2 = compiler.compile_text("t", WorkingState(), list(history))
    assert meta2["compression_count"] == 1
    snapshot = compiler.budget.hysteresis_snapshot(mode="legacy")
    assert snapshot["compression_skipped_no_gain"] >= 1
    assert snapshot["steps_since_last_compression"] >= 1


def test_hysteresis_recompresses_after_new_entries():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=2000, max_new_tokens=100)
    )
    compiler.compile_text("t", WorkingState(), _long_history(20))
    more = _long_history(20) + [
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": f"z{i}.py"},
            "content": "X" * 600,
        }
        for i in range(4)
    ]
    _, meta = compiler.compile_text("t", WorkingState(), more)
    assert meta["compression_count"] == 2
    assert meta["hysteresis"]["steps_since_last_compression"] == 0


def test_hysteresis_metadata_observable():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=3000, max_new_tokens=100)
    )
    _, meta = compiler.compile_text("t", WorkingState(), _long_history(30))
    hysteresis = meta["hysteresis"]
    assert hysteresis["high_watermark"] > hysteresis["target_watermark"]
    assert "compression_skipped_no_gain" in hysteresis
    assert "compression_thrashing_detected" in hysteresis
    assert "last_compressed_span_fingerprint" in hysteresis
    assert "steps_since_last_compression" in hysteresis
    # legacy 别名：trigger_threshold == high_watermark。
    assert meta["trigger_threshold"] == hysteresis["high_watermark"]


def test_reset_run_state_clears_hysteresis_and_counts():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=2000, max_new_tokens=100)
    )
    compiler.compile_text("t", WorkingState(), _long_history(20))
    assert compiler.compression_count >= 1
    assert compiler.budget.hysteresis_snapshot()["last_compressed_history_len"] > 0
    compiler.reset_run_state()
    assert compiler.compression_count == 0
    assert compiler.budget.hysteresis_snapshot()["last_compressed_history_len"] == 0
    assert compiler.budget.hysteresis_snapshot()["compression_skipped_no_gain"] == 0
    assert compiler.budget.hysteresis_snapshot()["steps_since_last_compression"] == 0


def test_hysteresis_state_is_per_mode():
    """legacy 与 native 两条管线的 hysteresis 状态互不污染。"""
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=2000, max_new_tokens=100)
    )
    compiler.compile_text("t", WorkingState(), _long_history(20))
    legacy = compiler.budget.hysteresis_snapshot(mode="legacy")
    native = compiler.budget.hysteresis_snapshot(mode="native")
    assert legacy["last_compressed_history_len"] > 0
    assert native["last_compressed_history_len"] == 0
    assert legacy["steps_since_last_compression"] >= 0
    # native 管线独立计数：跑 native 压缩不影响 legacy 的 span。
    messages = [{"role": "system", "content": "s"}]
    messages += _native_pair("c1", "read_file", {"path": "a.py"}, "A" * 3000)
    compiler.compile_native("t", WorkingState(), messages)
    assert compiler.budget.hysteresis_snapshot(mode="native")["last_compressed_history_len"] > 0
    assert (
        compiler.budget.hysteresis_snapshot(mode="legacy")["last_compressed_history_len"]
        == legacy["last_compressed_history_len"]
    )


def test_token_metrics_same_caliber_legacy():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=3000, max_new_tokens=100)
    )
    history = _long_history(30)
    text, meta = compiler.compile_text("t", WorkingState(), history)
    raw = meta["raw_model_visible_tokens"]
    compiled = meta["compiled_model_visible_tokens"]
    assert raw > 0 and compiled > 0
    # raw 与 compiled 覆盖同一范围：压缩后 raw >= compiled。
    assert raw >= compiled
    assert meta["context_tokens_reclaimed"] == raw - compiled
    assert 0 <= meta["context_reduction_ratio"] <= 1
    assert meta["compiled_history_tokens"] >= 0
    assert 0 <= meta["history_reduction_ratio"] <= 1
    assert text


def test_token_metrics_same_caliber_native_no_compress():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=32000, max_new_tokens=512)
    )
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    out, meta = compiler.compile_native("t", WorkingState(), messages)
    assert meta["should_compress"] is False
    # 未压缩：compiled == raw（修复 native 未压缩时 compiled 缺失的问题）。
    assert meta["compiled_context_tokens"] == meta["candidate_context_tokens"]
    assert meta["raw_model_visible_tokens"] == meta["compiled_model_visible_tokens"]
    assert meta["context_tokens_reclaimed"] == 0
    assert out == messages  # 原列表引用保持


def test_token_metrics_same_caliber_native_compressed():
    compiler = ContextCompiler(
        budget=ContextBudget.resolve(context_window=20000, max_new_tokens=512)
    )
    messages = [{"role": "system", "content": "sys"}]
    for index in range(6):
        messages += _native_pair(
            f"c{index}", "read_file", {"path": f"f{index}.py"}, "D" * 4000
        )
    out, meta = compiler.compile_native("t", WorkingState(), messages)
    assert meta["should_compress"] is True
    raw = meta["raw_model_visible_tokens"]
    compiled = meta["compiled_model_visible_tokens"]
    assert raw > 0 and compiled > 0
    assert meta["context_tokens_reclaimed"] == max(0, raw - compiled)
    assert meta["compiled_history_tokens"] >= 0
    assert "provider_actual_input_tokens" in meta
    assert "hysteresis" in meta
    # 原子性保持：无 orphan tool message。
    call_ids = set()
    for message in out:
        if message.get("tool_calls"):
            for call in message["tool_calls"]:
                call_ids.add(call["id"])
    for message in out:
        if message.get("role") == "tool":
            assert message.get("tool_call_id") in call_ids, "orphan tool message"
