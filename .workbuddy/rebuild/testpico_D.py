import os
import base64
import io
import json
import subprocess
import shlex
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

import codecub as mini_pkg
from codecub import (
    AnthropicCompatibleModelClient,
    FakeModelClient,
    MiniAgent,
    OllamaModelClient,
    OpenAICompatibleModelClient,
    SessionStore,
    WorkspaceContext,
    build_welcome,
)
from codecub.cli import HELP_DETAILS, build_arg_parser, decode_cwd_arg
from codecub.connections.presets import DEEPSEEK_OFFICIAL
from codecub.experiments.tasks import tasks_for_suite
from codecub import task_policy
from codecub.models import ModelResponse, ToolCall
from codecub.tools import native_tool_definitions


def build_workspace(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)


def build_agent(tmp_path, outputs, **kwargs):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".codecub" / "sessions")
    approval_policy = kwargs.pop("approval_policy", "auto")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=approval_policy,
        **kwargs,
    )


def test_default_max_steps_is_80(tmp_path):
    agent = build_agent(tmp_path, [])

    assert agent.max_steps is None
    assert agent.runtime_mode == "interactive"
    assert agent.effective_step_budget is None
    assert agent.emergency_cap == 500

    args = build_arg_parser().parse_args([])
    assert args.max_steps is None


def test_explicit_max_steps_override_still_creates_fixed_budget(tmp_path):
    agent = build_agent(tmp_path, [], max_steps=12)

    assert agent.max_steps == 12
    assert agent.effective_step_budget == 12


def test_allowed_tools_filter_prompt_visibility_but_runtime_still_rejects(tmp_path):
    agent = build_agent(
        tmp_path,
        [],
        allowed_tools=("read_file", "patch_file"),
    )

    assert set(agent.tools) == {"read_file", "patch_file"}
    assert "list_files(" not in agent.prefix
    assert '<tool name="write_file"' not in agent.prefix
    assert agent.run_tool("list_files", {"path": "."}).startswith("error: unknown tool")


def test_shell_environment_blocks_git_discovery_above_workspace(tmp_path):
    agent = build_agent(tmp_path, [])

    assert agent.shell_env()["GIT_DIR"] == os.devnull


def test_canonical_tool_specs_render_native_schema_without_drift(tmp_path):
    agent = build_agent(tmp_path, [], allowed_tools=("read_file", "patch_file"))
    definitions = native_tool_definitions(agent.tools)
    patch = next(
        item for item in definitions if item["function"]["name"] == "patch_file"
    )
    assert patch["function"]["parameters"] == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    }


def test_native_tool_protocol_uses_structured_calls_and_runtime_validation(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-test"
        last_completion_metadata = {}

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "search",
                            "search",
                            {"pattern": "old", "path": "target.py"},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "search",
                            "search",
                            {"pattern": "old", "path": "target.py"},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "call-1",
                            "patch_file",
                            {
                                "path": "target.py",
                                "old_text": "old = 1",
                                "new_text": "new = 1",
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "verify-1",
                            "run_shell",
                            {"command": "python -c \"print('verified')\""},
                        ),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(
            self, messages, tools, max_new_tokens, tool_choice="auto"
        ):
            self.requests.append((messages, tools, tool_choice))
            return self.responses.pop(0)

    (tmp_path / "target.py").write_text("old = 1\n", encoding="utf-8")
    client = NativeClient()
    agent = MiniAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        allowed_tools=("patch_file", "run_shell"),
        requires_workspace_change=True,
    )
    assert agent.ask("Update target.py") == "Done."
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "new = 1\n"
    assert client.requests[0][2] == "auto"
    assert {tool["function"]["name"] for tool in client.requests[0][1]} == {
        "patch_file",
        "run_shell",
    }
    assert "<tool>" not in client.requests[0][0][0]["content"]
    assert {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "patched target.py",
    } in client.requests[0][0]


def test_native_multi_tool_batch_is_sequential_and_preserves_each_call_id(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-test"
        last_completion_metadata = {}

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "read-1",
                            "read_file",
                            {"path": "a.txt", "start": 1, "end": 1},
                        ),
                        ToolCall(
                            "read-2",
                            "read_file",
                            {"path": "b.txt", "start": 1, "end": 1},
                        ),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(
            self, messages, tools, max_new_tokens, tool_choice="auto"
        ):
            self.requests.append(messages)
            return self.responses.pop(0)

    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    client = NativeClient()
    agent = MiniAgent(
        client,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
    )
    assert agent.ask("Inspect files") == "Done."
    tool_messages = [item for item in client.requests[-1] if item.get("role") == "tool"]
    assert [item["tool_call_id"] for item in tool_messages] == ["read-1", "read-2"]


def test_native_edit_decision_batch_keeps_tool_results_contiguous(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-no-tool-choice"
        last_completion_metadata = {}
        connection_profile = type("Profile", (), {"supports_tool_choice": False})()

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall("read-1", "read_file", {"path": "a.py", "start": 1, "end": 1}),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall("read-2", "read_file", {"path": "b.py", "start": 1, "end": 1}),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall("search", "search", {"path": ".", "pattern": "old"}),
                        ToolCall("read-3", "read_file", {"path": "a.py", "start": 1, "end": 1}),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
            self.requests.append(messages)
            return self.responses.pop(0)

    (tmp_path / "a.py").write_text("old = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("old = 2\n", encoding="utf-8")
    client = NativeClient()
    agent = MiniAgent(
        client,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        requires_workspace_change=True,
    )

    assert agent.ask("Repair the implementation") == "Done."
    batch = next(
        index
        for index, item in enumerate(client.requests[-1])
        if item.get("role") == "assistant" and item.get("tool_calls")
        and [call["id"] for call in item["tool_calls"]] == ["search", "read-3"]
    )
    assert [item["role"] for item in client.requests[-1][batch + 1 : batch + 3]] == [
        "tool",
        "tool",
    ]


def test_native_edit_decision_transitions_from_evidence_to_patch_and_verification(
    tmp_path,
):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-test"
        last_completion_metadata = {}

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "read",
                            "read_file",
                            {"path": "target.py", "start": 1, "end": 5},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "decision",
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
                            "verify",
                            "read_file",
                            {"path": "target.py", "start": 1, "end": 5},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "verify-shell",
                            "run_shell",
                            {"command": "python -c \"print('verified')\""},
                        ),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(
            self, messages, tools, max_new_tokens, tool_choice="auto"
        ):
            self.requests.append((messages, tools, tool_choice))
            return self.responses.pop(0)

    (tmp_path / "target.py").write_text("old = 1\n", encoding="utf-8")
    client = NativeClient()
    agent = MiniAgent(
        client,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        requires_workspace_change=True,
    )
    assert agent.ask("Repair target.py") == "Done."
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "new = 1\n"
    assert client.requests[1][2] == "auto"
    assert [item["function"]["name"] for item in client.requests[1][1]] == [
        "submit_edit_decision"
    ]
    report = json.loads(
        next((tmp_path / ".codecub" / "runs").glob("*/report.json")).read_text(
            encoding="utf-8"
        )
    )
    assert report["planning"]["edit_decision_count"] == 1
    assert report["planning"]["workspace_change_count"] == 1


def test_native_edit_decision_keeps_real_tools_when_tool_choice_is_unsupported(
    tmp_path,
):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-no-tool-choice"
        last_completion_metadata = {}
        connection_profile = type("Profile", (), {"supports_tool_choice": False})()

        def __init__(self):
            self.requests = []
            self.responses = [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "search",
                            "search",
                            {"pattern": "old", "path": "target.py"},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "read",
                            "read_file",
                            {"path": "target.py", "start": 1, "end": 5},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "read-second",
                            "read_file",
                            {"path": "target.py", "start": 1, "end": 1},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "patch",
                            "patch_file",
                            {
                                "path": "target.py",
                                "old_text": "old = 1",
                                "new_text": "new = 1",
                            },
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "verify",
                            "run_shell",
                            {"command": "python -c \"print('verified')\""},
                        ),
                    )
                ),
                ModelResponse(text="Done."),
            ]

        def complete_with_tools(
            self, messages, tools, max_new_tokens, tool_choice="auto"
        ):
            self.requests.append((messages, tools, tool_choice))
            return self.responses.pop(0)

    (tmp_path / "target.py").write_text("old = 1\n", encoding="utf-8")
    client = NativeClient()
    agent = MiniAgent(
        client,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        requires_workspace_change=True,
    )

    assert agent.ask("Repair target.py") == "Done."
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "new = 1\n"
    decision_tools = {
        item["function"]["name"] for item in client.requests[3][1]
    }
    assert {"patch_file", "read_file"} <= decision_tools
    assert "submit_edit_decision" not in decision_tools
    assert client.requests[3][2] is None
    report = json.loads(
        next((tmp_path / ".codecub" / "runs").glob("*/report.json")).read_text(
            encoding="utf-8"
        )
    )
    assert report["planning"]["edit_decision_count"] == 1
    assert report["planning"]["workspace_change_count"] == 1


def test_semantic_repeat_helpers_distinguish_overlap_and_fresh_ranges():
    first = {"path": "A.py", "start": 1, "end": 200}
    overlapping = {"path": "./a.py", "start": 20, "end": 200}
    next_page = {"path": "a.py", "start": 201, "end": 400}

    assert task_policy.is_semantic_repeat("read_file", overlapping, "read_file", first)
    assert not task_policy.is_semantic_repeat(
        "read_file", next_page, "read_file", first
    )
    assert task_policy.is_semantic_repeat(
        "search",
        {"path": "./codecub", "pattern": "Feature   Flags"},
        "search",
        {"path": "codecub", "pattern": "feature flags"},
    )
    assert (
        task_policy.normalize_shell_command({"command": "  PYTHON   -m pytest  -q "})
        == "python -m pytest -q"
    )


def test_exploration_warning_is_generic_and_patch_resets_counter(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    outputs = [
        f'<tool>{{"name":"search","args":{{"path":".","pattern":"term{index}"}}}}</tool>'
        for index in range(6)
    ] + [
        '<tool>{"name":"patch_file","args":{"path":"hello.txt","old_text":"alpha","new_text":"beta"}}</tool>',
        "<final>Done.</final>",
    ]
    agent = build_agent(tmp_path, outputs)

    assert agent.ask("Repair the file") == "Done."
    trace = [
        json.loads(line)
        for line in (tmp_path / ".codecub" / "runs")
        .glob("*/trace.jsonl")
        .__next__()
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    warning = next(event for event in trace if event["event"] == "exploration_warning")
    report = json.loads(
        next((tmp_path / ".codecub" / "runs").glob("*/report.json")).read_text(
            encoding="utf-8"
        )
    )

    assert "runtime.py" not in json.dumps(warning)
    assert report["planning"]["first_action_step"] == 7
    assert report["planning"]["exploration_steps_before_first_action"] == 6


def test_modification_contract_tracks_change_epochs_and_shell_verification(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    command = "python -c \"print('ok')\""
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"patch_file","args":{"path":"hello.txt","old_text":"alpha","new_text":"beta"}}</tool>',
            f'<tool>{{"name":"run_shell","args":{{"command":{json.dumps(command)}}}}}</tool>',
            f'<tool>{{"name":"run_shell","args":{{"command":{json.dumps(command)}}}}}</tool>',
            '<tool>{"name":"patch_file","args":{"path":"hello.txt","old_text":"beta","new_text":"gamma"}}</tool>',
            f'<tool>{{"name":"run_shell","args":{{"command":{json.dumps(command)}}}}}</tool>',
            "<final>Done.</final>",
        ],
        requires_workspace_change=True,
    )

    assert "requires an actual workspace modification" in agent.prefix
    assert agent.ask("Repair the workspace") == "Done."
    report = json.loads(
        next((tmp_path / ".codecub" / "runs").glob("*/report.json")).read_text(
            encoding="utf-8"
        )
    )
    planning = report["planning"]
    assert planning["workspace_change_count"] == 2
    assert planning["first_workspace_change_step"] == 1
    assert planning["first_execution_step"] == 2
    assert planning["first_verification_after_change_step"] == 2
    assert planning["verification_steps"] == 3
    assert planning["redundant_verification_steps"] == 1
    assert planning["productive_verification_steps"] == 2


def test_implementation_warning_requires_contract_and_two_shells(tmp_path):
    command_one = "python -c \"print('one')\""
    command_two = "python -c \"print('two')\""
    agent = build_agent(
        tmp_path,
        [
            f'<tool>{{"name":"run_shell","args":{{"command":{json.dumps(command_one)}}}}}</tool>',
            f'<tool>{{"name":"run_shell","args":{{"command":{json.dumps(command_two)}}}}}</tool>',
            "<final>Stopped.</final>",
        ],
        requires_workspace_change=True,
    )

    agent.ask("Repair the workspace")
    trace_path = next((tmp_path / ".codecub" / "runs").glob("*/trace.jsonl"))
    events = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    report = json.loads(
        next((tmp_path / ".codecub" / "runs").glob("*/report.json")).read_text(
            encoding="utf-8"
        )
    )
    assert sum(event["event"] == "implementation_warning" for event in events) == 1
    assert report["planning"]["verification_before_first_action"] == 2
    assert report["planning"]["implementation_warning_count"] == 1

    ordinary_path = tmp_path / "ordinary"
    ordinary_path.mkdir()
    ordinary = build_agent(ordinary_path, [], requires_workspace_change=False)
    ordinary.current_planning = ordinary.new_planning_state()
    ordinary.current_planning["verification_steps"] = 2
    assert ordinary.maybe_emit_implementation_warning(None) is False


def test_evidence_ledger_marks_visible_overlap_as_avoidable_and_softly_reminds(
    tmp_path,
):
    (tmp_path / "evidence.py").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"evidence.py","start":1,"end":3}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"evidence.py","start":1,"end":3}}</tool>',
            "<final>Done.</final>",
        ],
    )

    assert agent.ask("Inspect evidence.py") == "Done."
    run_dir = next((tmp_path / ".codecub" / "runs").glob("*"))
    trace = [
        json.loads(line)
        for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    reads = [
        event
        for event in trace
        if event["event"] == "tool_executed" and event["name"] == "read_file"
    ]
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    assert reads[1]["read_evidence_classification"] == "avoidable_repeated_read"
    assert "substantially overlaps source code" in reads[1]["result"]
    assert (
        "evidence.py"
        not in reads[1]["result"].split("Runtime notice:", 1)[1].split("\n", 1)[0]
    )
    assert report["planning"]["avoidable_repeated_read_calls"] == 1
    assert report["planning"]["evidence_evicted_reread_calls"] == 0
    prompts = [
        event["prompt_metadata"] for event in trace if event["event"] == "prompt_built"
    ]
    assert prompts[1]["inspected_evidence"]["visible_entry_count"] == 1


def test_evidence_ledger_distinguishes_eviction_freshness_and_new_ranges(tmp_path):
    (tmp_path / "evidence.py").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])
    agent.current_planning = agent.new_planning_state()
    agent.record_read_evidence(
        {"path": "evidence.py", "start": 1, "end": 3}, "# evidence.py\n1: alpha", 1
    )
    entry = agent.evidence_ledger_entries()[0]
    agent.last_prompt_metadata = {
        "inspected_evidence": {"entries": [{**entry, "visible": False}]}
    }

    assert (
        agent.assess_read_evidence({"path": "evidence.py", "start": 1, "end": 3})[0]
        == "evidence_evicted_reread"
    )
    assert (
        agent.assess_read_evidence({"path": "evidence.py", "start": 4, "end": 6})[0]
        == "new"
    )
    (tmp_path / "evidence.py").write_text("changed\n", encoding="utf-8")
    assert (
        agent.assess_read_evidence({"path": "evidence.py", "start": 1, "end": 3})[0]
        == "new"
    )


def test_evidence_ledger_is_bounded_and_redacts_compact_hints(tmp_path):
    (tmp_path / "evidence.py").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])
    agent.current_planning = agent.new_planning_state()
    for step in range(1, 9):
        agent.record_read_evidence(
            {"path": "evidence.py", "start": step, "end": step},
            f"# evidence.py\n{step}: API_KEY=not-a-real-secret-{step}",
            step,
        )

    ledger = agent.evidence_ledger_entries()
    assert len(ledger) == 6
    assert agent.current_planning["evidence_eviction_count"] == 2
    assert all("not-a-real-secret" not in entry["hint"] for entry in ledger)
    assert len(agent.evidence_ledger_text()) < 2200


def test_agent_runs_tool_then_final(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":2}}</tool>',
            "<final>Read the file successfully.</final>",
        ],
    )

    answer = agent.ask("Inspect hello.txt")

    assert answer == "Read the file successfully."
    assert any(
        item["role"] == "tool" and item["name"] == "read_file"
        for item in agent.session["history"]
    )
    assert "hello.txt" in agent.session["memory"]["files"]


def test_agent_updates_task_summary_on_each_request(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>First pass.</final>",
            "<final>Second pass.</final>",
        ],
    )

    assert agent.ask("First request") == "First pass."
    assert agent.session["memory"]["working"]["task_summary"] == "First request"

    assert agent.ask("Second request") == "Second pass."
    assert agent.session["memory"]["working"]["task_summary"] == "Second request"


def test_agent_only_stores_reusable_epistemic_notes(tmp_path):
    (tmp_path / "facts.txt").write_text("deploy key is red\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"facts.txt","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
            "<final>It is red.</final>",
        ],
    )

    assert agent.ask("Read the file and remember the fact") == "Done."
    notes = agent.session["memory"]["episodic_notes"]
    assert any("deploy key is red" in note["text"] for note in notes)
    assert not any(note["text"] == "Done." for note in notes)
    assert not any(note["text"] == "Done." for note in notes)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>It is red.</final>"]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("What color is the deploy key?") == "It is red."
    prompt = resumed.model_client.prompts[-1]
    assert "Relevant memory" in prompt
    assert "deploy key is red" in prompt


def test_file_summary_cache_is_invalidated_on_out_of_band_edit_and_path_spelling(
    tmp_path,
):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    agent.memory.set_file_summary("./sample.txt", "alpha")
    agent.memory.remember_file("./sample.txt")
    assert agent.memory.to_dict()["file_summaries"]["sample.txt"]["freshness"]

    assert (
        "file_summaries: available for sample.txt" in agent.memory.render_memory_text()
    )
    assert "sample.txt: alpha" not in agent.memory.render_memory_text()
    assert "sample.txt: alpha" in agent.memory.retrieval_view("sample.txt")
    file_path.write_text("beta\n", encoding="utf-8")

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient([]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert "sample.txt: alpha" not in resumed.memory_text()
    assert "sample.txt: alpha" not in resumed.memory.retrieval_view("sample.txt")
    resumed.memory.invalidate_file_summary("sample.txt")
    assert "sample.txt" not in resumed.memory.to_dict()["file_summaries"]


def test_memory_recall_debug_text_explains_relevant_memory(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])
    agent.memory.set_file_summary("sample.txt", "alpha")
    agent.memory.remember_file("sample.txt")

    view = agent.memory_recall_debug_text("what did sample.txt contain?")

    assert view.startswith("Relevant memory debug:\n")
    assert "Selected:\n1. sample.txt: alpha" in view
    assert "reason: path_match" in view


def test_agent_retries_after_empty_model_output(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "",
            "<final>Recovered after retry.</final>",
        ],
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after retry."
    notices = [
        item["content"]
        for item in agent.session["history"]
        if item["role"] == "assistant"
    ]
    assert any("empty response" in item for item in notices)


def test_agent_retries_when_model_omits_required_protocol_tags(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "unstructured planning text",
            "<final>Recovered after protocol retry.</final>",
        ],
    )

    assert agent.ask("Do the task") == "Recovered after protocol retry."
    notices = [
        item["content"]
        for item in agent.session["history"]
        if item["role"] == "assistant"
    ]
    assert any("missing required <tool> or <final> tags" in item for item in notices)


def test_code_explanation_requires_a_source_read_before_final(tmp_path):
    (tmp_path / "implementation.py").write_text(
        "def remember():\n    return True\n", encoding="utf-8"
    )
    agent = build_agent(
        tmp_path,
        [
            "<final>The design uses a memory module.</final>",
            '<tool>{"name":"read_file","args":{"path":"implementation.py","start":1,"end":2}}</tool>',
            "<final>The implementation is in implementation.py.</final>",
        ],
    )

    assert (
        agent.ask("这个代码上下文记忆怎么做的？")
        == "The implementation is in implementation.py."
    )
    assert any(
        "no source-file evidence" in item["content"]
        for item in agent.session["history"]
        if item["role"] == "assistant"
    )


def test_code_explanation_forces_final_after_research_budget(tmp_path):
    (tmp_path / "implementation.py").write_text(
        "def remember():\n    return True\n", encoding="utf-8"
    )
    reads = [
        '<tool>{"name":"read_file","args":{"path":"implementation.py","start":1,"end":2}}</tool>'
    ]
    reads.extend(
        f'<tool>{{"name":"search","args":{{"path":".","pattern":"remember{index}"}}}}</tool>'
        for index in range(5)
    )
    agent = build_agent(tmp_path, reads + ["<final>Evidence-based answer.</final>"])

    assert agent.ask("这个代码上下文记忆怎么做的？") == "Evidence-based answer."
    assert sum(item["role"] == "tool" for item in agent.session["history"]) == 6
    assert any(
        "research budget is exhausted" in item["content"]
        for item in agent.session["history"]
        if item["role"] == "assistant"
    )


def test_agent_retries_after_malformed_tool_payload(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":"bad"}</tool>',
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":1}}</tool>',
            "<final>Recovered after malformed tool output.</final>",
        ],
    )

    answer = agent.ask("Inspect hello.txt")

    assert answer == "Recovered after malformed tool output."
    assert any(
        item["role"] == "tool" and item["name"] == "read_file"
        for item in agent.session["history"]
    )
    notices = [
        item["content"]
        for item in agent.session["history"]
        if item["role"] == "assistant"
    ]
    assert any("valid <tool> call" in item for item in notices)


def test_agent_accepts_xml_write_file_tool(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool name="write_file" path="hello.py"><content>print("hi")\n</content></tool>',
            "<final>Done.</final>",
        ],
    )

    answer = agent.ask("Create hello.py")

    assert answer == "Done."
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == 'print("hi")\n'


def test_retries_do_not_consume_the_whole_budget(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "",
            "",
            "<final>Recovered after several retries.</final>",
        ],
        max_steps=1,
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after several retries."


def test_agent_saves_and_resumes_session(tmp_path):
    agent = build_agent(tmp_path, ["<final>First pass.</final>"])
    assert agent.ask("Start a session") == "First pass."

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.session["history"][0]["content"] == "Start a session"
    assert resumed.ask("Continue") == "Resumed."


def test_delegate_uses_child_agent(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"delegate","args":{"task":"inspect README","max_steps":2}}</tool>',
            "<final>Child result.</final>",
            "<final>Parent incorporated the child result.</final>",
        ],
    )

    answer = agent.ask("Use delegation")

    assert answer == "Parent incorporated the child result."
    tool_events = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert tool_events[0]["name"] == "delegate"
    assert "delegate_result" in tool_events[0]["content"]


def test_patch_file_replaces_exact_match(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello world\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "patch_file",
        {
            "path": "sample.txt",
            "old_text": "world",
            "new_text": "agent",
        },
    )

    assert result == "patched sample.txt"
    assert file_path.read_text(encoding="utf-8") == "hello agent\n"


def test_patch_contract_parser_and_rejections_preserve_multiline_arguments(tmp_path):
    agent = build_agent(tmp_path, [], allowed_tools=("read_file", "patch_file"))
    target = tmp_path / "sample.py"
    target.write_text(
        "alpha = '<&>'\nbeta = 1\nalpha = '<&>'\nbeta = 1\n", encoding="utf-8"
    )
    xml = (
        '<tool name="patch_file" path="sample.py"><old_text>alpha = \'<&>\'\nbeta = 1\n'
        "</old_text><new_text>alpha = 'fixed'\nbeta = 2\n</new_text></tool>"
    )
    parsed = MiniAgent.parse(xml)
    assert parsed == (
        "tool",
        {
            "name": "patch_file",
            "args": {
                "path": "sample.py",
                "old_text": "alpha = '<&>'\nbeta = 1\n",
                "new_text": "alpha = 'fixed'\nbeta = 2\n",
            },
        },
    )
    assert "occur exactly once, found 2" in agent.run_tool(
        "patch_file", parsed[1]["args"]
    )
    assert "occur exactly once, found 0" in agent.run_tool(
        "patch_file", {"path": "sample.py", "old_text": "missing", "new_text": "x"}
    )
    assert "missing new_text" in agent.run_tool(
        "patch_file", {"path": "sample.py", "old_text": "beta = 1"}
    )
    assert "path escapes workspace" in agent.run_tool(
        "patch_file", {"path": "../outside.py", "old_text": "x", "new_text": "y"}
    )
    assert agent.run_tool(
        "write_file", {"path": "blocked.py", "content": "x"}
    ).startswith("error: unknown tool")
    json_call = (
        '<tool>{"name":"patch_file","args":{"path":"sample.py",'
        '"old_text":"alpha = \'<&>\'\\nbeta = 1\\n",'
        '"new_text":"alpha = \'fixed\'\\nbeta = 2\\n"}}</tool>'
    )
    assert MiniAgent.parse(json_call)[1]["args"] == {
        "path": "sample.py",
        "old_text": "alpha = '<&>'\nbeta = 1\n",
        "new_text": "alpha = 'fixed'\nbeta = 2\n",
    }


def test_patch_contract_prompt_and_read_only_feedback_are_consistent(tmp_path):
    agent = build_agent(tmp_path, [], allowed_tools=("patch_file",))
    assert "<old_text>...</old_text><new_text>...</new_text>" in agent.prefix
    assert "<content>...</content>" not in agent.prefix
    (tmp_path / "sample.txt").write_text("old", encoding="utf-8")
    read_only = build_agent(tmp_path, [], read_only=True)
    result = read_only.run_tool(
        "patch_file", {"path": "sample.txt", "old_text": "old", "new_text": "new"}
    )
    assert result == "error: approval denied for patch_file"
    assert (
        read_only._last_tool_result_metadata["security_event_type"] == "read_only_block"
    )


def test_workspace_change_prompt_contract_is_explicit_and_scoped(tmp_path):
    modify_agent = build_agent(tmp_path, [], requires_workspace_change=True)
    inspect_agent = build_agent(tmp_path, [], requires_workspace_change=False)

    assert (
        "Analysis, repository inspection, and test execution alone do not complete it."
        in modify_agent.prefix
    )
    assert "actual workspace modification" in modify_agent.prefix
    assert "actual workspace modification" not in inspect_agent.prefix


def test_action_readiness_transitions_after_source_evidence_and_workspace_change(
    tmp_path,
):
    agent = build_agent(tmp_path, [], requires_workspace_change=True)
    agent.current_planning = agent.new_planning_state()

    agent.update_planning_state(
        "search", {"path": ".", "pattern": "target"}, {"tool_status": "ok"}, 1
    )
    assert agent.current_planning["action_readiness"] == "evidence_gathering"
    agent.update_planning_state(
        "read_file",
        {"path": "module.py", "start": 1, "end": 20},
        {"tool_status": "ok"},
        2,
    )
    assert agent.current_planning["action_readiness"] == "action_expected"
    assert "inspected relevant source evidence" in agent.action_readiness_text()
    agent.update_planning_state(
        "run_shell", {"command": "python -m pytest"}, {"tool_status": "ok"}, 3
    )
    assert agent.current_planning["action_readiness"] == "action_expected"
    agent.update_planning_state(
        "patch_file", {}, {"tool_status": "ok", "workspace_changed": True}, 4
    )
    assert agent.current_planning["action_readiness"] == "action_taken"
    agent.update_planning_state(
        "read_file",
        {"path": "module.py", "start": 21, "end": 40},
        {"tool_status": "ok"},
        5,
    )
    assert agent.current_planning["action_readiness"] == "action_taken"
    assert agent.current_planning["action_readiness_transitions"] == [
        {"state": "unknown", "tool_step": 0},
        {"state": "evidence_gathering", "tool_step": 1},
        {"state": "action_expected", "tool_step": 2},
        {"state": "action_taken", "tool_step": 4},
    ]


def test_tool_patch_contract_is_visible_and_executes_in_a_fresh_workspace(tmp_path):
    task = next(
        task
        for task in tasks_for_suite("development")
        if task.id == "tool_patch_contract"
    )
    target = tmp_path / task.path
    target.parent.mkdir(parents=True)
    target.write_text(
        f"def guarded_patch(count):\n{task.mutation}\n        return True\n",
        encoding="utf-8",
    )
    agent = build_agent(
        tmp_path,
        [
            f'<tool>{{"name":"read_file","args":{{"path":{json.dumps(task.path)},"start":1,"end":10}}}}</tool>',
            f'<tool>{{"name":"patch_file","args":{{"path":{json.dumps(task.path)},"old_text":{json.dumps(task.mutation)},"new_text":{json.dumps(task.baseline)}}}}}</tool>',
            "<final>Done.</final>",
        ],
        allowed_tools=task.allowed_tools,
        requires_workspace_change=task.requires_workspace_change,
    )

    assert task.requires_workspace_change is True
    assert "patch_file(path: str, old_text: str, new_text: str)" in agent.prefix
    assert "<old_text>return -1</old_text>" in agent.prefix
    assert agent.run_tool(
        "write_file", {"path": "blocked.py", "content": "x"}
    ).startswith("error: unknown tool")
    assert agent.ask(task.prompt) == "Done."

    report = json.loads(
        next((tmp_path / ".codecub" / "runs").glob("*/report.json")).read_text(
            encoding="utf-8"
        )
    )
    assert target.read_text(encoding="utf-8").count(task.baseline) == 1
    assert report["planning"]["workspace_change_count"] == 1
    assert report["planning"]["first_action_step"] == 2
    assert not report["planning"]["evidence_ledger"]
    assert MiniAgent.parse(
        '<tool name="patch_file" path="sample.py"><old_text>old</old_text><new_text>new</new_text></tool>'
    ) == (
        "tool",
        {
            "name": "patch_file",
            "args": {"path": "sample.py", "old_text": "old", "new_text": "new"},
        },
    )


def test_invalid_risky_tool_does_not_prompt_for_approval(tmp_path):
    agent = build_agent(tmp_path, [], approval_policy="ask")

    with patch("builtins.input") as mock_input:
        result = agent.run_tool("write_file", {})

    assert result.startswith("error: invalid arguments for write_file: 'path'")
    assert 'example: <tool name="write_file"' in result
    mock_input.assert_not_called()


def test_list_files_hides_internal_agent_state(tmp_path):
    agent = build_agent(tmp_path, [])
    (tmp_path / ".codecub").mkdir(exist_ok=True)
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / "hello.txt").write_text("hi\n", encoding="utf-8")

    result = agent.run_tool("list_files", {})

    assert ".codecub" not in result
    assert ".git" not in result
    assert "[F] hello.txt" in result


def test_repeated_identical_tool_call_is_rejected(tmp_path):
    agent = build_agent(tmp_path, [])
    for index in range(4):
        agent.record(
            {
                "role": "tool",
                "name": "list_files",
                "args": {},
                "content": "(empty)",
                "created_at": str(index),
            }
        )

    result = agent.run_tool("list_files", {})

    assert result == (
        "error: repeated identical tool call for list_files; "
        "same no-progress action reached 5 consecutive attempts"
    )


def test_repeated_identical_tool_call_does_not_count_previous_user_turn(tmp_path):
    agent = build_agent(tmp_path, [])
    for index in range(4):
        agent.record(
            {
                "role": "tool",
                "name": "list_files",
                "args": {},
                "content": "(empty)",
                "created_at": str(index),
            }
        )
    agent.record(
        {"role": "user", "content": "new request", "created_at": "after-previous-run"}
    )

    result = agent.run_tool("list_files", {})

    assert result.startswith("[")


def test_agent_enters_recovery_turn_after_repeated_no_progress_tool_calls(tmp_path):
    repeated = '<tool>{"name":"list_files","args":{}}</tool>'
    agent = build_agent(
        tmp_path,
        [
            repeated,
            repeated,
            repeated,
            repeated,
            repeated,
            "<final>Done.</final>",
        ],
    )

    answer = agent.ask("Inspect the repository")

    # Phase 1: 相同无进展动作不再直接停机，而是进入 STUCK_SUSPECTED ->
    # Recovery Turn；模型随后给出 final 则正常完成。
    assert answer == "Done."
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(
            encoding="utf-8"
        )
    )
    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert report["status"] == "completed"
    assert report["stop_reason"] == "final_answer_returned"
    assert report["watchdog"]["stuck_suspected_count"] == 1
    assert report["watchdog"]["recovery_turn_count"] == 1
    assert report["watchdog"]["stuck_pattern"] == "identical_loop"
    assert any(
        event["event"] == "stuck_suspected"
        and event["pattern"] == "identical_loop"
        for event in trace_events
    )
    assert any(
        event["event"] == "recovery_turn_started" for event in trace_events
    )
    assert any(
        event["event"] == "tool_executed"
        and event.get("tool_error_code") == "repeated_identical_call"
        for event in trace_events
    )
    assert report["watchdog"]["stuck_confirmed_count"] == 0


def test_welcome_screen_keeps_box_shape_for_long_paths(tmp_path):
    deep = (
        tmp_path
        / "very"
        / "long"
        / "path"
        / "for"
        / "the"
        / "mini"
        / "agent"
        / "welcome"
        / "screen"
    )
    deep.mkdir(parents=True)
    agent = build_agent(deep, [])

    welcome = build_welcome(agent, model="qwen3.5:4b", host="http://127.0.0.1:11434")
    lines = welcome.splitlines()

    assert len(lines) >= 5
    assert len({len(line) for line in lines}) == 1
    assert "..." in welcome
    assert "(  o o  )" in welcome
    assert "MINI-CODING-AGENT" not in welcome
    assert "MINI CODING AGENT" not in welcome
    assert "CodeCub" in welcome
    assert "local coding agent" in welcome
    assert "// READY" not in welcome
    assert "SLASH" not in welcome
    assert "READY      " not in welcome
    assert "commands: Commands:" not in welcome


def test_help_details_lists_memory_recall_command():
    assert "/memory recall <query>" in HELP_DETAILS


def test_ollama_client_posts_expected_payload():
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"response": "<final>ok</final>"}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OllamaModelClient(
        model="qwen3.5:4b",
        host="http://127.0.0.1:11434",
        temperature=0.2,
        top_p=0.9,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "http://127.0.0.1:11434/api/generate"
    assert captured["timeout"] == 30
    assert captured["body"]["model"] == "qwen3.5:4b"
    assert captured["body"]["prompt"] == "hello"
    assert captured["body"]["stream"] is False


def test_openai_compatible_client_posts_expected_responses_payload():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"output_text": "<final>ok</final>"}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "https://right.codes/v1/responses"
    assert captured["timeout"] == 30
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"] == {
        "model": "right.codes/codex-mini",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "hello",
                    }
                ],
            }
        ],
        "max_output_tokens": 42,
        "stream": False,
        "temperature": 0.2,
    }


def test_openai_compatible_client_sends_prompt_cache_fields_and_records_usage():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "output_text": "<final>ok</final>",
                    "usage": {
                        "input_tokens": 2048,
                        "input_tokens_details": {"cached_tokens": 1536},
                        "output_tokens": 32,
                        "total_tokens": 2080,
                    },
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete(
            "hello",
            42,
            prompt_cache_key="prefix-hash-123",
            prompt_cache_retention="in_memory",
        )

    assert result == "<final>ok</final>"
    assert captured["body"]["prompt_cache_key"] == "prefix-hash-123"
    assert captured["body"]["prompt_cache_retention"] == "in_memory"
    assert client.last_completion_metadata["prompt_cache_supported"] is True
    assert client.last_completion_metadata["cached_tokens"] == 1536
    assert client.last_completion_metadata["cache_hit"] is True
    assert client.last_completion_metadata["input_tokens"] == 2048


def test_openai_compatible_client_extracts_text_from_event_stream():
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return (
                'data: {"type":"response.created","response":{"id":"resp_1","output":[]}}\n'
                'data: {"type":"response.completed","response":{"output":[{"content":[{"text":"<final>stream ok</final>"}]}]}}\n'
                "data: [DONE]\n"
            ).encode("utf-8")

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = client.complete("hello", 42)

    assert result == "<final>stream ok</final>"


def test_openai_compatible_client_extracts_text_from_event_stream_deltas():
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return (
                "event: response.output_text.delta\n"
                'data: {"type":"response.output_text.delta","delta":"<final>"}\n'
                "event: response.output_text.delta\n"
                'data: {"type":"response.output_text.delta","delta":"OK"}\n'
                "event: response.output_text.done\n"
                'data: {"type":"response.output_text.done","text":"<final>OK</final>"}\n'
                "data: [DONE]\n"
            ).encode("utf-8")

    client = OpenAICompatibleModelClient(
        model="right.codes/codex-mini",
        base_url="https://right.codes/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = client.complete("hello", 42)

    assert result == "<final>OK</final>"


def test_openai_compatible_client_falls_back_to_chat_completions_when_responses_endpoint_is_missing():
    requests = []

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "<final>chat ok</final>",
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "total_tokens": 13,
                    },
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        if request.full_url.endswith("/responses"):
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                None,
                io.BytesIO(b'{"error":"not found"}'),
            )
        return FakeResponse()

    client = OpenAICompatibleModelClient(
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>chat ok</final>"
    assert [item["url"] for item in requests] == [
        "https://dashscope.aliyuncs.com/compatible-mode/v1/responses",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    ]
    assert requests[1]["timeout"] == 30
    assert requests[1]["body"] == {
        "model": "qwen-plus",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 42,
        "stream": False,
        "temperature": 0.2,
    }


def test_deepseek_native_tools_omit_unsupported_tool_choice():
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = OpenAICompatibleModelClient(
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        temperature=None,
        timeout=30,
        connection_profile=DEEPSEEK_OFFICIAL,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete_with_tools(
            [{"role": "user", "content": "inspect the workspace"}],
            [{"type": "function", "function": {"name": "search", "parameters": {}}}],
            42,
            tool_choice=None,
        )

    assert result.text == "done"
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert "tool_choice" not in captured["body"]
    assert captured["body"]["parallel_tool_calls"] is False


def test_openai_compatible_stream_falls_back_to_chat_completions_when_responses_endpoint_is_missing():
    requests = []

    class FakeStreamResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            lines = [
                b'data: {"choices":[{"delta":{"content":"<final>"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"stream ok"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"</final>"}}],"usage":{"prompt_tokens":8,"completion_tokens":4,"total_tokens":12}}\n',
                b"data: [DONE]\n",
            ]
            return iter(lines)

    def fake_urlopen(request, timeout):
        requests.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        if request.full_url.endswith("/responses"):
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "Not Found",
                None,
                io.BytesIO(b'{"error":"not found"}'),
            )
        return FakeStreamResponse()

    client = OpenAICompatibleModelClient(
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test",
        temperature=None,
        timeout=30,
    )
    chunks = []

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.stream_complete("hello", 42, chunks.append)

    assert result == "<final>stream ok</final>"
    assert chunks == ["<final>", "stream ok", "</final>"]
    assert [item["url"] for item in requests] == [
        "https://dashscope.aliyuncs.com/compatible-mode/v1/responses",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    ]
    assert requests[1]["body"] == {
        "model": "qwen-plus",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 42,
        "stream": True,
    }


def test_openai_compatible_client_does_not_fallback_on_auth_errors():
    urls = []

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            None,
            io.BytesIO(b'{"error":"bad key"}'),
        )

    client = OpenAICompatibleModelClient(
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="bad-key",
        temperature=None,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        with pytest.raises(RuntimeError, match="HTTP 401"):
            client.complete("hello", 42)

    assert urls == ["https://dashscope.aliyuncs.com/compatible-mode/v1/responses"]


def test_anthropic_compatible_client_posts_expected_messages_payload():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "<final>ok</final>",
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    client = AnthropicCompatibleModelClient(
        model="claude-sonnet-4-5-20250929",
        base_url="https://www.right.codes/claude-aws/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"
    assert captured["url"] == "https://www.right.codes/claude-aws/v1/messages"
    assert captured["timeout"] == 30
    assert captured["headers"]["X-api-key"] == "sk-test"
    assert captured["headers"]["Anthropic-version"] == "2023-06-01"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"] == {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "hello",
                    }
                ],
            }
        ],
        "max_tokens": 42,
        "stream": False,
        "temperature": 0.2,
    }


def test_anthropic_compatible_client_extracts_first_text_block():
    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "content": [
                        {"type": "thinking", "thinking": "hidden"},
                        {"type": "text", "text": "<final>ok</final>"},
                    ]
                }
            ).encode("utf-8")

    client = AnthropicCompatibleModelClient(
        model="claude-sonnet-4-5-20250929",
        base_url="https://www.right.codes/claude-aws/v1",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = client.complete("hello", 42)

    assert result == "<final>ok</final>"


def test_build_agent_uses_openai_provider_and_model_override(tmp_path):
    args = type(
        "Args",
        (),
        {
            "cwd": str(tmp_path),
            "provider": "openai",
            "model": "override-model",
            "base_url": None,
            "host": "http://127.0.0.1:11434",
            "ollama_timeout": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "resume": None,
            "approval": "ask",
            "secret_env_names": [],
            "max_steps": 6,
            "max_new_tokens": 512,
        },
    )()

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_BASE": "https://www.right.codes/codex/v1",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "env-model",
        },
        clear=False,
    ):
        with (
            patch(
                "codecub.cli.OllamaModelClient",
                side_effect=AssertionError("ollama client should not be used"),
            ),
            patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai,
        ):
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "override-model"
    assert (
        mock_openai.call_args.kwargs["base_url"] == "https://www.right.codes/codex/v1"
    )
    assert mock_openai.call_args.kwargs["api_key"] == "sk-test"
    assert agent.model_client is fake_client


def test_build_arg_parser_leaves_provider_unset_for_env_default(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    assert args.provider is None


def test_build_arg_parser_accepts_anthropic_provider(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(
        ["--cwd", str(tmp_path), "--provider", "anthropic"]
    )

    assert args.provider == "anthropic"


def test_build_arg_parser_accepts_hosted_provider_presets(tmp_path):
    for provider in ("deepseek", "kimi", "minimax"):
        args = mini_pkg.build_arg_parser().parse_args(
            ["--cwd", str(tmp_path), "--provider", provider]
        )

        assert args.provider == provider


def test_build_arg_parser_accepts_app_mode_flags():
    app_args = mini_pkg.build_arg_parser().parse_args(["--app-mode"])
    json_args = mini_pkg.build_arg_parser().parse_args(["--json-events"])

    assert app_args.app_mode is True
    assert json_args.app_mode is True


def test_build_arg_parser_decodes_utf8_base64_cwd():
    cwd = "D:\\代码备份\\项目"
    encoded = base64.b64encode(cwd.encode("utf-8")).decode("ascii")

    args = mini_pkg.build_arg_parser().parse_args(["--cwd-b64", encoded])

    assert decode_cwd_arg(args) == cwd


def test_main_dispatches_to_app_mode_without_welcome(monkeypatch, capsys):
    from codecub import cli

    called = {}

    def fake_run_app_mode(args):
        called["app_mode"] = args.app_mode
        print(
            '{"type":"session_started","timestamp":"2026-06-11T00:00:00Z","session_id":"s","run_id":"","payload":{}}'
        )
        return 0

    monkeypatch.setattr(cli, "run_app_mode", fake_run_app_mode)

    result = cli.main(["--app-mode"])

    captured = capsys.readouterr()
    assert result == 0
    assert called["app_mode"] is True
    assert "codecub>" not in captured.out
    assert "session_started" in captured.out


def test_main_interactive_cli_shows_activity_stream(monkeypatch, capsys):
    from codecub import cli

    class FakeWorkspace:
        cwd = "D:/repo"
        branch = "main"

    class FakeModelClient:
        model = "qwen-test"

    class FakeAgent:
        workspace = FakeWorkspace()
        model_client = FakeModelClient()
        approval_policy = "ask"
        session = {"id": "s1"}
        session_path = "D:/repo/.codecub/sessions/s1.json"
        event_handler = None

        def ask(self, message):
            assert message == "change ui"
            self.event_handler(
                "run_status", {"phase": "building_context"}, self, object()
            )
            self.event_handler(
                "run_status", {"phase": "model_streaming"}, self, object()
            )
            self.event_handler("assistant_delta", {"text": "done"}, self, object())
            return "done"

    inputs = iter(["change ui", "/exit"])
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return next(inputs)

    monkeypatch.setattr(cli, "build_agent", lambda args: FakeAgent())
    monkeypatch.setattr("builtins.input", fake_input)

    result = cli.main([])

    captured = capsys.readouterr()
    assert result == 0
    assert any("codecub>" in prompt for prompt in prompts)
    assert all("codecub ›" not in prompt for prompt in prompts)
    assert "Building context" in captured.out
    assert "Receiving model response" in captured.out
    assert "assistant" in captured.out
    assert "done" in captured.out


def test_build_agent_uses_anthropic_provider_and_openai_key_fallback(tmp_path):
    args = type(
        "Args",
        (),
        {
            "cwd": str(tmp_path),
            "provider": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "base_url": None,
            "host": "http://127.0.0.1:11434",
            "ollama_timeout": 300,
            "openai_timeout": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "resume": None,
            "approval": "ask",
            "secret_env_names": [],
            "max_steps": 6,
            "max_new_tokens": 512,
        },
    )()

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-openai-fallback",
        },
        clear=True,
    ):
        with (
            patch(
                "codecub.cli.OllamaModelClient",
                side_effect=AssertionError("ollama client should not be used"),
            ),
            patch(
                "codecub.cli.OpenAICompatibleModelClient",
                side_effect=AssertionError("openai client should not be used"),
            ),
            patch("codecub.cli.AnthropicCompatibleModelClient") as mock_anthropic,
        ):
            fake_client = mock_anthropic.return_value
            agent = mini_pkg.build_agent(args)

    mock_anthropic.assert_called_once()
    assert mock_anthropic.call_args.kwargs["model"] == "claude-sonnet-4-5-20250929"
    assert (
        mock_anthropic.call_args.kwargs["base_url"]
        == "https://www.right.codes/claude/v1"
    )
    assert mock_anthropic.call_args.kwargs["api_key"] == "sk-openai-fallback"
    assert agent.model_client is fake_client


def test_build_agent_uses_anthropic_default_model_when_env_is_missing(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(
        ["--cwd", str(tmp_path), "--provider", "anthropic"]
    )

    with patch.dict(
        os.environ,
        {},
        clear=False,
    ):
        os.environ.pop("ANTHROPIC_MODEL", None)
        with patch("codecub.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            mini_pkg.build_agent(args)

    assert mock_anthropic.call_args.kwargs["model"] == "claude-sonnet-4-6"


def test_build_agent_uses_openai_provider_by_default(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_BASE": "https://www.right.codes/codex/v1",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "qwen-flash",
        },
        clear=False,
    ):
        with (
            patch(
                "codecub.cli.OllamaModelClient",
                side_effect=AssertionError("ollama client should not be used"),
            ),
            patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai,
        ):
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "qwen-flash"
    assert (
        mock_openai.call_args.kwargs["base_url"] == "https://www.right.codes/codex/v1"
    )
    assert mock_openai.call_args.kwargs["api_key"] == "sk-test"
    assert agent.model_client is fake_client


def test_parse_env_file_supports_quotes_export_and_comments(tmp_path):
    from codecub.cli import parse_env_file

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# local model config",
                'export OPENAI_API_KEY="sk quoted # keep"',
                "OPENAI_MODEL='env-model'",
                "OPENAI_API_BASE=https://env.example/v1 # strip comment",
                "EMPTY_VALUE=",
                "1_BAD_KEY=ignored",
                "MISSING_SEPARATOR",
            ]
        ),
        encoding="utf-8",
    )

    values = parse_env_file(env_path)

    assert values == {
        "OPENAI_API_KEY": "sk quoted # keep",
        "OPENAI_MODEL": "env-model",
        "OPENAI_API_BASE": "https://env.example/v1",
        "EMPTY_VALUE": "",
    }


def test_build_agent_loads_dotenv_values_from_workspace_root(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_BASE=https://env.example/v1",
                'OPENAI_API_KEY="sk-env"',
                "OPENAI_MODEL=env-model",
            ]
        ),
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(os.environ, {}, clear=True):
        with patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "env-model"
    assert mock_openai.call_args.kwargs["base_url"] == "https://env.example/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-env"
    assert agent.model_client is fake_client


def test_build_agent_keeps_system_env_over_dotenv_values(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_BASE=https://env.example/v1",
                "OPENAI_API_KEY=sk-env",
                "OPENAI_MODEL=env-model",
            ]
        ),
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-system"}, clear=True):
        with patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai:
            mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "env-model"
    assert mock_openai.call_args.kwargs["base_url"] == "https://env.example/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-system"


def test_build_agent_uses_dotenv_provider_for_deepseek(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODECUB_PROVIDER=deepseek",
                "DEEPSEEK_API_BASE=https://api.deepseek.com",
                "DEEPSEEK_API_KEY=sk-deepseek-env",
                "DEEPSEEK_MODEL=deepseek-coder",
            ]
        ),
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(os.environ, {}, clear=True):
        with patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "deepseek-coder"
    assert mock_openai.call_args.kwargs["base_url"] == "https://api.deepseek.com"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-deepseek-env"
    assert agent.model_client is fake_client


def test_deepseek_uses_current_official_default_model_when_unset(tmp_path):
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=sk-deepseek-env\n",
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(
        ["--cwd", str(tmp_path), "--provider", "deepseek"]
    )

    with patch.dict(os.environ, {}, clear=True):
        with patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai:
            mini_pkg.build_agent(args)

    assert mock_openai.call_args.kwargs["model"] == "deepseek-v4-flash"
    assert mock_openai.call_args.kwargs["base_url"] == "https://api.deepseek.com"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-deepseek-env"


def test_build_agent_uses_dotenv_provider_for_kimi(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODECUB_PROVIDER=kimi",
                "MOONSHOT_API_BASE=https://api.moonshot.cn/v1",
                "MOONSHOT_API_KEY=sk-kimi-env",
                "MOONSHOT_MODEL=moonshot-v1-32k",
            ]
        ),
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(os.environ, {}, clear=True):
        with patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "moonshot-v1-32k"
    assert mock_openai.call_args.kwargs["base_url"] == "https://api.moonshot.cn/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-kimi-env"
    assert agent.model_client is fake_client


def test_build_agent_uses_dotenv_provider_for_minimax(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODECUB_PROVIDER=minimax",
                "MINIMAX_API_BASE=https://api.minimax.io/v1",
                "MINIMAX_API_KEY=sk-minimax-env",
                "MINIMAX_MODEL=MiniMax-M3",
            ]
        ),
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(os.environ, {}, clear=True):
        with patch("codecub.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "MiniMax-M3"
    assert mock_openai.call_args.kwargs["base_url"] == "https://api.minimax.io/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-minimax-env"
    assert agent.model_client is fake_client


def test_build_agent_uses_dotenv_provider_for_anthropic(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODECUB_PROVIDER=anthropic",
                "ANTHROPIC_API_BASE=https://anthropic.env/v1",
                "ANTHROPIC_API_KEY=sk-anthropic-env",
                "ANTHROPIC_MODEL=claude-env",
            ]
        ),
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(os.environ, {}, clear=True):
        with patch("codecub.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            fake_client = mock_anthropic.return_value
            agent = mini_pkg.build_agent(args)

    mock_anthropic.assert_called_once()
    assert mock_anthropic.call_args.kwargs["model"] == "claude-env"
    assert mock_anthropic.call_args.kwargs["base_url"] == "https://anthropic.env/v1"
    assert mock_anthropic.call_args.kwargs["api_key"] == "sk-anthropic-env"
    assert agent.model_client is fake_client


def test_build_agent_uses_dotenv_ollama_model_and_host(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CODECUB_PROVIDER=ollama",
                "OLLAMA_MODEL=llama-env",
                "OLLAMA_HOST=http://127.0.0.1:11435",
            ]
        ),
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(os.environ, {}, clear=True):
        with patch("codecub.cli.OllamaModelClient") as mock_ollama:
            fake_client = mock_ollama.return_value
            agent = mini_pkg.build_agent(args)

    mock_ollama.assert_called_once()
    assert mock_ollama.call_args.kwargs["model"] == "llama-env"
    assert mock_ollama.call_args.kwargs["host"] == "http://127.0.0.1:11435"
    assert agent.model_client is fake_client


def test_successful_run_persists_run_artifacts_and_stop_reason(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":2}}</tool>',
            "<final>Finished.</final>",
        ],
    )

    assert agent.ask("Do the thing") == "Finished."

    runs_root = tmp_path / ".codecub" / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    task_state = json.loads((run_dir / "task_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()

    assert task_state["task_id"] != task_state["run_id"]
    assert run_dir.name == task_state["run_id"]
    assert (run_dir / "task_state.json").exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "report.json").exists()
    assert task_state["stop_reason"] == "final_answer_returned"
    assert task_state["final_answer"] == "Finished."
    assert report["stop_reason"] == "final_answer_returned"
    assert report["task_state"]["stop_reason"] == "final_answer_returned"
    assert report["run_id"] == task_state["run_id"]
    trace_events = [json.loads(line)["event"] for line in trace_lines]
    assert trace_events[0] == "run_started"
    assert trace_events[-1] == "run_finished"
    assert trace_events.count("prompt_built") == 2
    assert "tool_executed" in trace_events


def test_ask_uses_supplied_run_id_for_run_artifacts(tmp_path):
    agent = build_agent(tmp_path, ["<final>Finished.</final>"])

    assert agent.ask("Do the thing", run_id="run-app-1") == "Finished."

    run_dir = tmp_path / ".codecub" / "runs" / "run-app-1"
    task_state = json.loads((run_dir / "task_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    assert run_dir.exists()
    assert (run_dir / "trace.jsonl").exists()
    assert task_state["run_id"] == "run-app-1"
    assert report["run_id"] == "run-app-1"
    assert report["task_state"]["run_id"] == "run-app-1"


def test_ask_traces_context_steps_before_model_request(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])

    assert agent.ask("Inspect the repository", run_id="run-context-steps") == "Done."

    trace_path = tmp_path / ".codecub" / "runs" / "run-context-steps" / "trace.jsonl"
    trace_events = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    event_names = [event["event"] for event in trace_events]
    context_phases = [
        event["phase"]
        for event in trace_events
        if event["event"] == "context_step_started"
    ]

    assert context_phases[:3] == [
        "checking_workspace",
        "loading_memory",
        "building_prompt",
    ]
    assert event_names.index("context_step_started") < event_names.index("prompt_built")
    assert event_names.index("prompt_built") < event_names.index("model_requested")


def test_ask_rejects_external_run_id_that_escapes_run_directory(tmp_path):
    agent = build_agent(tmp_path, ["<final>Finished.</final>"])

    with pytest.raises(ValueError, match="invalid run_id"):
        agent.ask("Do the thing", run_id="../escape")

    assert not (tmp_path / ".codecub" / "escape").exists()
    assert not (tmp_path / ".codecub" / "runs" / ".." / "escape").resolve().exists()


def test_ask_stops_and_persists_report_when_canceled_before_model_request(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.cancel_checker = lambda runtime, task_state: (
        task_state.run_id == "run-cancel-1"
    )

    assert agent.ask("Cancel this run", run_id="run-cancel-1") == "Canceled by user."

    run_dir = tmp_path / ".codecub" / "runs" / "run-cancel-1"
    task_state = json.loads((run_dir / "task_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    trace_events = [
        json.loads(line)["event"]
        for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert task_state["status"] == "stopped"
    assert task_state["stop_reason"] == "user_canceled"
    assert task_state["final_answer"] == "Canceled by user."
    assert report["stop_reason"] == "user_canceled"
    assert report["task_state"]["run_id"] == "run-cancel-1"
    assert trace_events[-1] == "run_canceled"
    assert "model_requested" not in trace_events


def test_ask_persists_report_when_model_raises(tmp_path):
    agent = build_agent(tmp_path, [])

    answer = agent.ask("Trigger model failure", run_id="run-model-error-1")

    run_dir = tmp_path / ".codecub" / "runs" / "run-model-error-1"
    task_state = json.loads((run_dir / "task_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    trace_events = [json.loads(line) for line in trace_lines]

    assert answer == "Model error: fake model ran out of outputs"
    assert task_state["status"] == "failed"
    assert task_state["stop_reason"] == "model_error"
    assert report["status"] == "failed"
    assert report["stop_reason"] == "model_error"
    assert report["task_state"]["run_id"] == "run-model-error-1"
    model_errors = [event for event in trace_events if event["event"] == "model_error"]
    assert len(model_errors) == 1
    assert model_errors[0]["error_type"] == "RuntimeError"
    assert model_errors[0]["message"] == "fake model ran out of outputs"


def test_model_error_trace_and_report_redact_secret_values(tmp_path):
    secret = "sk-test-secret-123"

    class SecretErrorModelClient:
        supports_prompt_cache = False

        def complete(self, prompt, max_new_tokens, **kwargs):
            del prompt, max_new_tokens, kwargs
            raise RuntimeError(f"backend down: {secret}")

    with patch.dict(os.environ, {"OPENAI_API_KEY": secret}, clear=False):
        workspace = build_workspace(tmp_path)
        store = SessionStore(tmp_path / ".codecub" / "sessions")
        agent = MiniAgent(
            model_client=SecretErrorModelClient(),
            workspace=workspace,
            session_store=store,
            approval_policy="auto",
        )

        assert (
            agent.ask("Trigger secret model failure", run_id="run-model-error-secret")
            == "Model error: backend down: <redacted>"
        )

    run_dir = tmp_path / ".codecub" / "runs" / "run-model-error-secret"
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    report_text = (run_dir / "report.json").read_text(encoding="utf-8")

    assert secret not in trace_text
    assert secret not in report_text
    assert "<redacted>" in trace_text
    assert "<redacted>" in report_text


def test_trace_and_report_redact_secret_env_values(tmp_path):
    secret = "sk-test-secret-123"
    script = f"print({secret!r})"
    if os.name == "nt":
        command = subprocess.list2cmdline([sys.executable, "-c", script])
    else:
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    with patch.dict(os.environ, {"OPENAI_API_KEY": secret}, clear=False):
        agent = build_agent(
            tmp_path,
            [
                f'<tool>{{"name":"run_shell","args":{{"command":{json.dumps(command)},"timeout":20}}}}</tool>',
                "<final>Masked.</final>",
            ],
        )

        assert agent.ask("Mask the secret") == "Masked."

    runs_root = tmp_path / ".codecub" / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    report_text = (run_dir / "report.json").read_text(encoding="utf-8")
    trace_events = [json.loads(line) for line in trace_text.splitlines()]

    assert secret not in trace_text
    assert secret not in report_text

    prompt_events = [
        event for event in trace_events if event["event"] == "prompt_built"
    ]
    assert prompt_events
    assert prompt_events[0]["prompt_metadata"]["secret_env_count"] >= 1
    assert "OPENAI_API_KEY" in prompt_events[0]["prompt_metadata"]["secret_env_names"]

    tool_events = [event for event in trace_events if event["event"] == "tool_executed"]
    assert tool_events
    assert "<redacted>" in tool_events[0]["args"]["command"]
    assert "<redacted>" in tool_events[0]["result"]


def test_prompt_budget_metadata_records_budget_decisions(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    agent.memory.append_note(
        "alpha episodic note " + ("A" * 120),
        tags=("recall",),
        created_at="2026-04-07T10:00:00+00:00",
    )
    agent.memory.append_note(
        "beta episodic recall note " + ("B" * 120),
        created_at="2026-04-07T10:01:00+00:00",
    )
    agent.memory.append_note(
        "gamma episodic note " + ("C" * 120),
        tags=("recall",),
        created_at="2026-04-07T10:02:00+00:00",
    )

    for index in range(4):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"history-{index}-" + ("A" * 240),
                "created_at": f"2026-04-07T10:0{index}:00+00:00",
            }
        )

    agent.context_manager.total_budget = 1000
    agent.context_manager.section_budgets = {
        "prefix": 80,
        "memory": 80,
        "relevant_memory": 80,
        "history": 80,
    }

    assert agent.ask("recall") == "Done."

    trace_events = [
        json.loads(line)
        for line in (
            agent.run_store.trace_path(agent.current_task_state)
            .read_text(encoding="utf-8")
            .splitlines()
        )
    ]
    prompt_events = [
        event for event in trace_events if event["event"] == "prompt_built"
    ]
    assert prompt_events
    metadata = prompt_events[0]["prompt_metadata"]
    relevant_section = (
        agent.model_client.prompts[0]
        .split("Relevant memory:\n", 1)[1]
        .split("\n\nTranscript:", 1)[0]
    )

    assert metadata["relevant_memory"]["selected_count"] == 2
    assert len(metadata["relevant_memory"]["rendered_notes"]) == 2
    assert (
        len([line for line in relevant_section.splitlines() if line.startswith("- ")])
        == 2
    )
    assert "alpha episodic" in relevant_section
    assert "gamma episodic" in relevant_section
    assert "beta episodic" not in relevant_section
    assert metadata["current_request"]["text"] == "recall"
    assert metadata["current_request"]["rendered_chars"] == len("recall")


def test_prompt_metadata_refreshes_prefix_when_workspace_changes(tmp_path):
    agent = build_agent(tmp_path, [])

    first = agent.prompt_metadata("first", "")
    second = agent.prompt_metadata("second", "")

    assert first["prefix_hash"] == second["prefix_hash"]
    assert second["prefix_changed"] is False
    assert second["workspace_changed"] is False

    (tmp_path / "README.md").write_text("demo changed\n", encoding="utf-8")

    third = agent.prompt_metadata("third", "")

    assert third["prefix_hash"] != second["prefix_hash"]
    assert third["prefix_changed"] is True
    assert third["workspace_changed"] is True
    assert "demo changed" in agent.prefix


def test_agent_creates_checkpoint_when_context_reduction_happens_and_artifacts_only_reference_it(
    tmp_path,
):
    agent = build_agent(tmp_path, ["<final>Done after checkpoint.</final>"])
    for index in range(10):
        agent.record(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"history-{index}-" + ("A" * 260),
                "created_at": f"2026-04-07T10:{index:02d}:00+00:00",
            }
        )
    agent.memory.append_note(
        "checkpoint note " + ("B" * 220),
        tags=("checkpoint",),
        created_at="2026-04-07T11:00:00+00:00",
    )
    agent.context_manager.total_budget = 900
    agent.context_manager.section_budgets = {
        "prefix": 120,
        "memory": 120,
        "relevant_memory": 120,
        "history": 160,
    }

    assert agent.ask("Resume the long task") == "Done after checkpoint."

    checkpoint_state = agent.session["checkpoints"]
    checkpoint = checkpoint_state["items"][checkpoint_state["current_id"]]
    assert checkpoint["checkpoint_id"] == checkpoint_state["current_id"]
    assert checkpoint["schema_version"] == "phase1-v1"
    assert checkpoint["current_goal"] == "Resume the long task"
    assert checkpoint["key_files"] == []
    assert checkpoint["current_blocker"] == ""
    assert checkpoint["next_step"]

    task_state = json.loads(
        agent.run_store.task_state_path(agent.current_task_state).read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(
            encoding="utf-8"
        )
    )
    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert task_state["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert report["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert report["task_state"]["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert "current_goal" not in task_state
    assert "current_goal" not in report
    checkpoint_events = [
        event for event in trace_events if event["event"] == "checkpoint_created"
    ]
    assert checkpoint_events
    assert checkpoint_events[-1]["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert "current_goal" not in checkpoint_events[-1]


def test_resume_prompt_uses_checkpoint_state_not_just_history(tmp_path):
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_manual",
        "items": {
            "ckpt_manual": {
                "checkpoint_id": "ckpt_manual",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Fix failing resume flow",
                "completed": ["Read runtime.py"],
                "excluded": ["Do not add branch summary"],
                "current_blocker": "Need to re-anchor stale file facts",
                "next_step": "Re-read runtime.py and refresh the checkpoint",
                "key_files": [{"path": "runtime.py", "freshness": "abc"}],
                "freshness": {"runtime.py": "abc"},
                "summary": "Resume from the latest checkpoint",
                "runtime_identity": {"workspace_fingerprint": "old-fingerprint"},
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."

    prompt = resumed.model_client.prompts[-1]
    assert "Task checkpoint:" in prompt
    assert "Current goal: Fix failing resume flow" in prompt
    assert "Current blocker: Need to re-anchor stale file facts" in prompt
    assert "Next step: Re-read runtime.py and refresh the checkpoint" in prompt


def test_resume_invalidates_stale_file_summaries_and_marks_partial_stale(tmp_path):
    file_path = tmp_path / "runtime.py"
    file_path.write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.memory.set_file_summary("runtime.py", "runtime.py: alpha")
    freshness = agent.memory.to_dict()["file_summaries"]["runtime.py"]["freshness"]
    agent.session["checkpoints"] = {
        "current_id": "ckpt_stale",
        "items": {
            "ckpt_stale": {
                "checkpoint_id": "ckpt_stale",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Fix stale summary handling",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Re-read runtime.py",
                "key_files": [{"path": "runtime.py", "freshness": freshness}],
                "freshness": {"runtime.py": freshness},
                "summary": "runtime.py is important",
                "runtime_identity": {
                    "workspace_fingerprint": agent.workspace.fingerprint()
                },
            }
        },
    }
    agent.session_store.save(agent.session)
    file_path.write_text("beta\n", encoding="utf-8")

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."

    assert "runtime.py" not in resumed.memory.to_dict()["file_summaries"]
    assert resumed.last_prompt_metadata["resume_status"] == "partial-stale"
    assert resumed.last_prompt_metadata["stale_summary_invalidations"] == 1


def test_run_shell_nonzero_with_workspace_change_is_recorded_as_partial_success(
    tmp_path,
):
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "run_shell",
        {
            "command": "printf 'changed\\n' > README.md && exit 1",
            "timeout": 20,
        },
    )

    assert "exit_code: 1" in result
    assert agent._last_tool_result_metadata["tool_status"] == "partial_success"
    assert agent._last_tool_result_metadata["affected_paths"] == ["README.md"]
    assert agent._last_tool_result_metadata["workspace_changed"] is True


def test_resume_marks_workspace_mismatch_when_checkpoint_runtime_identity_is_stale(
    tmp_path,
):
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_workspace",
        "items": {
            "ckpt_workspace": {
                "checkpoint_id": "ckpt_workspace",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Continue after drift",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Rebuild runtime state",
                "key_files": [],
                "freshness": {},
                "summary": "workspace changed",
                "runtime_identity": {"workspace_fingerprint": "outdated-fingerprint"},
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."
    assert resumed.last_prompt_metadata["resume_status"] == "workspace-mismatch"


def test_write_file_trace_records_minimum_tool_contract_fields(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"write_file","args":{"path":"notes.txt","content":"hello\\n"}}</tool>',
            "<final>Done.</final>",
        ],
    )

    assert agent.ask("Create notes.txt") == "Done."

    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    tool_event = [event for event in trace_events if event["event"] == "tool_executed"][
        -1
    ]

    assert tool_event["name"] == "write_file"
    assert tool_event["risk_level"] == "high"
    assert tool_event["read_only"] is False
    assert tool_event["tool_status"] == "ok"
    assert tool_event["affected_paths"] == ["notes.txt"]
    assert tool_event["workspace_changed"] is True
    assert tool_event["diff_summary"] == ["created:notes.txt"]


def test_resume_marks_schema_mismatch_when_checkpoint_version_is_incompatible(tmp_path):
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_schema",
        "items": {
            "ckpt_schema": {
                "checkpoint_id": "ckpt_schema",
                "parent_checkpoint_id": "",
                "schema_version": "legacy-v0",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Continue after schema change",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Migrate checkpoint",
                "key_files": [],
                "freshness": {},
                "summary": "schema changed",
                "runtime_identity": {
                    "workspace_fingerprint": agent.workspace.fingerprint()
                },
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."
    assert resumed.last_prompt_metadata["resume_status"] == "schema-mismatch"


def test_resume_marks_no_checkpoint_when_session_has_no_checkpoint_state(tmp_path):
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session.pop("checkpoints", None)
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue the task") == "Resumed."
    assert resumed.last_prompt_metadata["resume_status"] == "no-checkpoint"
    assert "Task checkpoint:" not in resumed.model_client.prompts[-1]


def test_freshness_mismatch_creates_checkpoint_before_model_completion(tmp_path):
    file_path = tmp_path / "runtime.py"
    file_path.write_text("alpha\n", encoding="utf-8")
    agent = build_agent(tmp_path, ["<final>Resumed.</final>"])
    agent.memory.set_file_summary("runtime.py", "runtime.py: alpha")
    freshness = agent.memory.to_dict()["file_summaries"]["runtime.py"]["freshness"]
    agent.session["checkpoints"] = {
        "current_id": "ckpt_freshness",
        "items": {
            "ckpt_freshness": {
                "checkpoint_id": "ckpt_freshness",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Handle freshness mismatch",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Re-read runtime.py",
                "key_files": [{"path": "runtime.py", "freshness": freshness}],
                "freshness": {"runtime.py": freshness},
                "summary": "runtime.py changed",
                "runtime_identity": {
                    "workspace_fingerprint": agent.workspace.fingerprint()
                },
            }
        },
    }
    agent.session_store.save(agent.session)
    file_path.write_text("beta\n", encoding="utf-8")

    assert agent.ask("Continue the task") == "Resumed."

    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    checkpoint_events = [
        event for event in trace_events if event["event"] == "checkpoint_created"
    ]

    assert checkpoint_events
    assert checkpoint_events[0]["trigger"] == "freshness_mismatch"


def test_runtime_identity_persists_key_execution_metadata(tmp_path):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".codecub" / "sessions")
    agent = MiniAgent(
        model_client=FakeModelClient(["<final>Done.</final>"]),
        workspace=workspace,
        session_store=store,
        approval_policy="never",
        max_steps=9,
        max_new_tokens=1024,
        feature_flags={"memory": True, "relevant_memory": False},
    )

    runtime_identity = agent.session["runtime_identity"]

    assert runtime_identity["session_id"] == agent.session["id"]
    assert runtime_identity["cwd"] == str(tmp_path)
    assert runtime_identity["approval_policy"] == "never"
    assert runtime_identity["read_only"] is False
    assert runtime_identity["max_steps"] == 9
    assert runtime_identity["max_new_tokens"] == 1024
    assert runtime_identity["feature_flags"]["memory"] is True
    assert runtime_identity["feature_flags"]["relevant_memory"] is False
    assert runtime_identity["shell_env_allowlist"] == list(agent.shell_env_allowlist)


def test_resume_records_runtime_identity_mismatch_fields_in_metadata_and_trace(
    tmp_path,
):
    agent = build_agent(tmp_path, ["<final>checkpoint ready.</final>"])
    agent.session["checkpoints"] = {
        "current_id": "ckpt_identity",
        "items": {
            "ckpt_identity": {
                "checkpoint_id": "ckpt_identity",
                "parent_checkpoint_id": "",
                "schema_version": "phase1-v1",
                "created_at": "2026-04-14T09:00:00+00:00",
                "current_goal": "Resume with a different runtime identity",
                "completed": [],
                "excluded": [],
                "current_blocker": "",
                "next_step": "Rebuild runtime identity",
                "key_files": [],
                "freshness": {},
                "summary": "identity changed",
                "runtime_identity": {
                    "workspace_fingerprint": agent.workspace.fingerprint(),
                    "approval_policy": "auto",
                    "read_only": False,
                    "max_steps": 6,
                    "max_new_tokens": 512,
                    "model": "old-model",
                    "model_client": "FakeModelClient",
                    "feature_flags": {"memory": True, "relevant_memory": True},
                    "shell_env_allowlist": ["PATH"],
                    "session_id": agent.session["id"],
                    "cwd": str(tmp_path),
                },
            }
        },
    }
    agent.session_store.save(agent.session)

    resumed = MiniAgent.from_session(
        model_client=FakeModelClient(["<final>Resumed.</final>"]),
        workspace=build_workspace(tmp_path),
        session_store=agent.session_store,
        session_id=agent.session["id"],
        approval_policy="never",
        max_steps=9,
        max_new_tokens=1024,
        feature_flags={"memory": True, "relevant_memory": False},
    )

    resumed.ask("Continue the task")

    assert resumed.last_prompt_metadata["resume_status"] == "workspace-mismatch"
    assert resumed.last_prompt_metadata["runtime_identity_mismatch_fields"] == [
        "approval_policy",
        "feature_flags",
        "max_new_tokens",
        "max_steps",
        "model",
        "shell_env_allowlist",
    ]

    trace_events = [
        json.loads(line)
        for line in resumed.run_store.trace_path(resumed.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    mismatch_events = [
        event for event in trace_events if event["event"] == "runtime_identity_mismatch"
    ]
    assert mismatch_events
    assert mismatch_events[0]["fields"] == [
        "approval_policy",
        "feature_flags",
        "max_new_tokens",
        "max_steps",
        "model",
        "shell_env_allowlist",
    ]


def test_partial_success_creates_process_note_for_exploration_history(tmp_path):
    agent = build_agent(tmp_path, [])

    agent.run_tool(
        "run_shell",
        {
            "command": "printf 'changed\\n' > README.md && exit 1",
            "timeout": 20,
        },
    )

    process_notes = [
        note
        for note in agent.memory.to_dict()["episodic_notes"]
        if note.get("kind") == "process"
    ]

    assert process_notes
    assert (
        process_notes[-1]["text"]
        == "run_shell partial_success on README.md; inspect diff before retry"
    )
    assert "partial_success" in process_notes[-1]["tags"]
    assert "README.md" in process_notes[-1]["tags"]


def test_explicit_memory_promotion_persists_durable_memory_topics(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>Project convention: Use constrained tools instead of guessing.\n"
            "Project convention: Preserve local agent state under .codecub/.\n"
            "Decision: Keep durable memory topic-based and lightweight.</final>",
        ],
    )

    answer = agent.ask(
        "Capture the stable facts you already discovered as durable memory. "
        "Respond with exactly the long-term facts."
    )

    assert "Project convention:" in answer

    index_path = tmp_path / ".codecub" / "memory" / "MEMORY.md"
    conventions_path = (
        tmp_path / ".codecub" / "memory" / "topics" / "project-conventions.md"
    )
    decisions_path = tmp_path / ".codecub" / "memory" / "topics" / "key-decisions.md"
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(
            encoding="utf-8"
        )
    )

    assert index_path.exists()
    assert conventions_path.exists()
    assert decisions_path.exists()
    assert "project-conventions" in index_path.read_text(encoding="utf-8")
    assert "Use constrained tools instead of guessing." in conventions_path.read_text(
        encoding="utf-8"
    )
    assert (
        "Keep durable memory topic-based and lightweight."
        in decisions_path.read_text(encoding="utf-8")
    )
    assert report["durable_promotions"] == [
        "project-conventions: Use constrained tools instead of guessing.",
        "project-conventions: Preserve local agent state under .codecub/.",
        "key-decisions: Keep durable memory topic-based and lightweight.",
    ]


def test_explicit_memory_promotion_supports_chinese_intent_and_labels(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>项目约定：优先使用受约束工具，不要靠猜。\n"
            "决策：持久记忆保持轻量、按 topic 管理。</final>",
        ],
    )

    answer = agent.ask("请把下面这些稳定事实记住，作为长期记忆保存下来。")

    assert "项目约定：" in answer

    conventions_path = (
        tmp_path / ".codecub" / "memory" / "topics" / "project-conventions.md"
    )
    decisions_path = tmp_path / ".codecub" / "memory" / "topics" / "key-decisions.md"

    assert "优先使用受约束工具，不要靠猜。" in conventions_path.read_text(
        encoding="utf-8"
    )
    assert "持久记忆保持轻量、按 topic 管理。" in decisions_path.read_text(
        encoding="utf-8"
    )


def test_explicit_memory_promotion_rejects_secret_shaped_and_transient_lines(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>Project convention: Use constrained tools instead of guessing.\n"
            "Dependency: API key is sk-live-secret-abc.\n"
            "Decision: Current goal is fix flaky tests.\n"
            "Dependency: stdout: FAIL test_one FAIL test_two FAIL test_three.</final>",
        ],
    )

    agent.ask("Capture these stable facts into durable memory.")

    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(
            encoding="utf-8"
        )
    )
    conventions_path = (
        tmp_path / ".codecub" / "memory" / "topics" / "project-conventions.md"
    )
    dependency_path = (
        tmp_path / ".codecub" / "memory" / "topics" / "dependency-facts.md"
    )

    assert report["durable_promotions"] == [
        "project-conventions: Use constrained tools instead of guessing.",
    ]
    assert report["durable_rejections"] == [
        "dependency-facts:secret_shaped",
        "key-decisions:transient_task_state",
        "dependency-facts:noisy_output",
    ]
    assert "Use constrained tools instead of guessing." in conventions_path.read_text(
        encoding="utf-8"
    )
    assert not dependency_path.exists()


def test_explicit_memory_promotion_supersedes_matching_durable_fact(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>Dependency: Python runtime is 3.11.</final>",
            "<final>Dependency: Python runtime is 3.12.</final>",
        ],
    )

    assert (
        agent.ask("Capture this stable dependency fact into durable memory.")
        == "Dependency: Python runtime is 3.11."
    )
    assert (
        agent.ask("Save the updated dependency fact into durable memory.")
        == "Dependency: Python runtime is 3.12."
    )

    dependency_path = (
        tmp_path / ".codecub" / "memory" / "topics" / "dependency-facts.md"
    )
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text(
            encoding="utf-8"
        )
    )
    text = dependency_path.read_text(encoding="utf-8")

    assert "Python runtime is 3.12." in text
    assert "Python runtime is 3.11." not in text
    assert report["durable_superseded"] == [
        "dependency-facts: Python runtime is 3.11. -> Python runtime is 3.12.",
    ]


def test_explicit_memory_promotion_dedupes_duplicate_durable_note(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>Project convention: Use constrained tools instead of guessing.</final>",
            "<final>Project convention: Use constrained tools instead of guessing.</final>",
        ],
    )

    agent.ask("Capture the stable fact into durable memory.")
    agent.ask("Capture the stable fact into durable memory again.")

    conventions_path = (
        tmp_path / ".codecub" / "memory" / "topics" / "project-conventions.md"
    )
    text = conventions_path.read_text(encoding="utf-8")

    assert text.count("Use constrained tools instead of guessing.") == 1


def test_agent_records_model_cache_metadata_in_last_prompt_metadata(tmp_path):
    class CacheAwareFakeModelClient(FakeModelClient):
        def complete(self, prompt, max_new_tokens, **kwargs):
            self.last_completion_metadata = {
                "prompt_cache_supported": True,
                "cached_tokens": 512,
                "cache_hit": True,
                "input_tokens": 1024,
            }
            return super().complete(prompt, max_new_tokens, **kwargs)

    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".codecub" / "sessions")
    agent = MiniAgent(
        model_client=CacheAwareFakeModelClient(["<final>Done.</final>"]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
    )

    assert agent.ask("Cache aware run") == "Done."

    assert agent.last_prompt_metadata["prompt_cache_supported"] is True
    assert agent.last_prompt_metadata["cached_tokens"] == 512
    assert agent.last_prompt_metadata["cache_hit"] is True
    assert agent.last_prompt_metadata["prefix_hash"]
    assert (
        agent.last_prompt_metadata["prompt_cache_key"]
        == agent.last_prompt_metadata["prefix_hash"]
    )


def test_recent_transcript_entries_stay_richer_than_older_ones(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])
    old_text = "OLD-" + ("A" * 320)
    recent_text = "RECENT-" + ("B" * 320)

    agent.record(
        {"role": "user", "content": old_text, "created_at": "2026-04-07T09:00:00+00:00"}
    )
    agent.record(
        {
            "role": "assistant",
            "content": old_text,
            "created_at": "2026-04-07T09:01:00+00:00",
        }
    )
    agent.record(
        {
            "role": "user",
            "content": recent_text,
            "created_at": "2026-04-07T09:02:00+00:00",
        }
    )
    agent.record(
        {
            "role": "assistant",
            "content": recent_text,
            "created_at": "2026-04-07T09:03:00+00:00",
        }
    )
    agent.record(
        {
            "role": "user",
            "content": recent_text,
            "created_at": "2026-04-07T09:04:00+00:00",
        }
    )
    agent.record(
        {
            "role": "assistant",
            "content": recent_text,
            "created_at": "2026-04-07T09:05:00+00:00",
        }
    )
    agent.record(
        {
            "role": "user",
            "content": recent_text,
            "created_at": "2026-04-07T09:06:00+00:00",
        }
    )
    agent.record(
        {
            "role": "assistant",
            "content": recent_text,
            "created_at": "2026-04-07T09:07:00+00:00",
        }
    )

    assert agent.ask("Check the transcript") == "Done."

    prompt = agent.model_client.prompts[-1]

    assert recent_text in prompt
    assert old_text not in prompt


def test_public_api_exports_resolve_through_package_path():
    assert callable(build_welcome)
    assert FakeModelClient is not None
    assert MiniAgent is not None
    assert OllamaModelClient is not None
    assert SessionStore is not None
    assert WorkspaceContext is not None
    assert Path(mini_pkg.__file__).as_posix().endswith("/codecub/__init__.py")


def test_reviewer_skeleton_docs_exist():
    review_pack = Path("docs/review-pack/README.md")
    architecture = Path("docs/architecture/agent-harness-v1-overview.md")

    if not review_pack.exists() or not architecture.exists():
        pytest.skip("reviewer skeleton docs are not present in this checkout")

    assert review_pack.exists()
    assert architecture.exists()

    review_text = review_pack.read_text(encoding="utf-8")
    assert "Project pitch" in review_text
    assert "Architecture map" in review_text
    assert "Benchmark evidence" in review_text
    assert "Sample run artifact list" in review_text

    architecture_text = architecture.read_text(encoding="utf-8")
    assert "Agent Harness v1" in architecture_text
    assert "task state" in architecture_text.lower()


def test_package_import_surface_includes_cli_entrypoints():
    assert callable(mini_pkg.main)
    assert callable(mini_pkg.build_agent)
    assert callable(mini_pkg.build_arg_parser)


def test_module_execution_help_works():
    result = subprocess.run(
        [sys.executable, "-m", "codecub", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert "--app-mode" in result.stdout
