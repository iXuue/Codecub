import argparse
import io
import json
import re
import time
from pathlib import Path

from codecub.app_runner import run_app_mode
from codecub.models import FakeModelClient
from codecub.runtime import Pico, SessionStore
from codecub.workspace import WorkspaceContext


def parse_jsonl(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def make_args(tmp_path):
    return argparse.Namespace(
        cwd=str(tmp_path),
        provider="openai",
        model=None,
        base_url=None,
        host="http://127.0.0.1:11434",
        temperature=0.2,
        top_p=0.9,
        ollama_timeout=300,
        openai_timeout=300,
        resume=None,
        approval="never",
        max_steps=6,
        max_new_tokens=512,
        secret_env_names=[],
    )


class ApprovalAwareStdin:
    def __init__(self, stdout, lines):
        self.stdout = stdout
        self.lines = list(lines)
        self.index = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.index >= len(self.lines):
            raise StopIteration
        wait_for, line = self.lines[self.index]
        self.index += 1
        if wait_for:
            deadline = time.monotonic() + 5
            while wait_for not in self.stdout.getvalue():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"timed out waiting for {wait_for}")
                time.sleep(0.01)
        return line


class NonStreamingFakeModelClient:
    supports_prompt_cache = False

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []
        self.last_completion_metadata = {}

    def complete(self, prompt, max_new_tokens, **kwargs):
        del max_new_tokens, kwargs
        self.prompts.append(prompt)
        if not self.outputs:
            raise RuntimeError("fake model ran out of outputs")
        return self.outputs.pop(0)


class SlowNonStreamingFakeModelClient(NonStreamingFakeModelClient):
    def complete(self, prompt, max_new_tokens, **kwargs):
        time.sleep(0.08)
        return super().complete(prompt, max_new_tokens, **kwargs)


def fake_agent_factory(outputs):
    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        store = SessionStore(Path(args.cwd) / ".codecub" / "sessions")
        return Pico(
            model_client=FakeModelClient(list(outputs)),
            workspace=workspace,
            session_store=store,
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    return build


def test_app_runner_emits_session_user_assistant_and_completion_events(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","message":"say hello"}\n'),
            ('"type":"run_completed"', '{"type":"close"}\n'),
        ],
    )

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(["<final>Hello from app mode.</final>"]),
    )

    events = parse_jsonl(stdout.getvalue())
    primary_events = [event for event in events if event["type"] != "run_status"]
    event_types = [event["type"] for event in primary_events]

    assert exit_code == 0
    assert event_types == [
        "session_started",
        "user_message_received",
        "assistant_delta",
        "assistant_message",
        "run_completed",
        "session_closed",
    ]
    assert primary_events[0]["session_id"]
    assert primary_events[1]["payload"]["message"] == "say hello"
    assert primary_events[2]["payload"]["text"] == "Hello from app mode."
    assert primary_events[3]["payload"]["text"] == "Hello from app mode."
    assert primary_events[4]["payload"]["final"] == "Hello from app mode."
    run_id = primary_events[1]["run_id"]
    assert re.fullmatch(r"run_\d{8}-\d{6}-\d{6}", run_id)
    assert primary_events[4]["run_id"] == run_id
    assert Path(primary_events[4]["payload"]["run_dir"]).name == run_id


def test_app_runner_preserves_unicode_messages_over_ascii_safe_jsonl(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdout = io.StringIO()
    command = json.dumps({"type": "send_message", "message": "查看我的代码"}, ensure_ascii=True)
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", f"{command}\n"),
            ('"type":"run_completed"', '{"type":"close"}\n'),
        ],
    )

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(["<final>已经查看。</final>"]),
    )

    output = stdout.getvalue()
    events = parse_jsonl(output)
    user_event = next(event for event in events if event["type"] == "user_message_received")
    assistant_event = next(event for event in events if event["type"] == "assistant_message")

    assert exit_code == 0
    assert "查看我的代码" not in output
    assert "已经查看" not in output
    assert user_event["payload"]["message"] == "查看我的代码"
    assert assistant_event["payload"]["text"] == "已经查看。"


def test_app_runner_uses_command_run_id_for_runtime_artifacts(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","run_id":"run-ui-1","message":"say hello"}\n'),
            ('"type":"run_completed"', '{"type":"close"}\n'),
        ],
    )

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(["<final>Hello from app mode.</final>"]),
    )

    events = parse_jsonl(stdout.getvalue())
    user_events = [event for event in events if event["type"] == "user_message_received"]
    assistant_events = [event for event in events if event["type"] == "assistant_message"]
    completed_events = [event for event in events if event["type"] == "run_completed"]

    assert exit_code == 0
    assert user_events[0]["run_id"] == "run-ui-1"
    assert assistant_events[0]["run_id"] == "run-ui-1"
    assert completed_events[0]["run_id"] == "run-ui-1"

    report_path = Path(completed_events[0]["payload"]["report_path"])
    run_dir = Path(completed_events[0]["payload"]["run_dir"])
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert run_dir == tmp_path / ".codecub" / "runs" / "run-ui-1"
    assert report_path == run_dir / "report.json"
    assert Path(completed_events[0]["payload"]["trace_path"]) == run_dir / "trace.jsonl"
    assert report["run_id"] == "run-ui-1"
    assert report["task_state"]["run_id"] == "run-ui-1"


def test_app_runner_emits_streamed_deltas_before_final_message(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO('{"type":"send_message","message":"say hello"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        client = FakeModelClient(["<final>Hello streamed answer.</final>"])
        client.stream_chunks = ["<final>Hello ", "streamed ", "answer.</final>"]
        return Pico(
            model_client=client,
            workspace=workspace,
            session_store=SessionStore(Path(args.cwd) / ".codecub" / "sessions"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    run_app_mode(make_args(tmp_path), stdin=stdin, stdout=stdout, agent_factory=build)
    events = parse_jsonl(stdout.getvalue())

    delta_events = [event for event in events if event["type"] == "assistant_delta"]
    final_index = next(index for index, event in enumerate(events) if event["type"] == "assistant_message")
    assert [event["payload"]["text"] for event in delta_events] == ["Hello ", "streamed ", "answer."]
    assert all(events.index(event) < final_index for event in delta_events)
    assert any(event["type"] == "run_status" and event["payload"]["phase"] == "model_streaming" for event in events)


def test_app_runner_emits_single_compatibility_delta_for_non_streaming_client(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO('{"type":"send_message","message":"say hello"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        return Pico(
            model_client=NonStreamingFakeModelClient(["<final>Hello from compatibility.</final>"]),
            workspace=workspace,
            session_store=SessionStore(Path(args.cwd) / ".codecub" / "sessions"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=build,
    )

    events = parse_jsonl(stdout.getvalue())
    deltas = [event for event in events if event["type"] == "assistant_delta"]

    assert exit_code == 0
    assert len(deltas) == 1
    assert deltas[0]["payload"]["text"] == "Hello from compatibility."


def test_app_runner_emits_heartbeat_status_while_run_is_active(tmp_path, monkeypatch):
    import codecub.app_runner as app_runner

    monkeypatch.setattr(app_runner, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","message":"inspect repository"}\n'),
            ('"heartbeat":true', '{"type":"close"}\n'),
        ],
    )

    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        return Pico(
            model_client=SlowNonStreamingFakeModelClient(["<final>Done.</final>"]),
            workspace=workspace,
            session_store=SessionStore(Path(args.cwd) / ".codecub" / "sessions"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    exit_code = run_app_mode(make_args(tmp_path), stdin=stdin, stdout=stdout, agent_factory=build)

    events = parse_jsonl(stdout.getvalue())
    heartbeats = [event for event in events if event["type"] == "run_status" and event["payload"].get("heartbeat") is True]
    assert exit_code == 0
    assert heartbeats
    assert heartbeats[0]["payload"]["phase"]
    assert heartbeats[0]["payload"]["silent_for_ms"] >= 0


def test_app_runner_emits_run_failed_for_model_error(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO('{"type":"send_message","message":"fail"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory([]),
    )

    events = parse_jsonl(stdout.getvalue())
    failed = [event for event in events if event["type"] == "run_failed"]

    assert exit_code == 0
    assert len(failed) == 1
    assert failed[0]["payload"]["error_type"] == "RuntimeError"
    assert "fake model ran out of outputs" in failed[0]["payload"]["message"]
    report_path = Path(failed[0]["payload"]["report_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["stop_reason"] == "model_error"
    assert report["run_id"] == failed[0]["run_id"]


def test_app_runner_accepts_cancel_current_run_command(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO('{"type":"cancel_run","run_id":"run-manual"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory([]),
    )

    events = parse_jsonl(stdout.getvalue())
    canceled = [event for event in events if event["type"] == "run_canceled"]

    assert exit_code == 0
    assert len(canceled) == 1
    assert canceled[0]["run_id"] == "run-manual"
    assert canceled[0]["payload"]["reason"] == "user_requested"


def test_app_runner_cancel_stops_active_runtime_and_writes_canceled_report(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    args = make_args(tmp_path)
    args.approval = "ask"
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","run_id":"run-cancel-1","message":"write"}\n'),
            ('"type":"approval_requested"', '{"type":"cancel_run","run_id":"run-cancel-1"}\n'),
            ('"type":"run_canceled"', '{"type":"close"}\n'),
        ],
    )

    exit_code = run_app_mode(
        args,
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(
            [
                '<tool name="write_file" path="canceled.txt"><content>no\n</content></tool>',
                "<final>should not complete</final>",
            ]
        ),
    )

    events = parse_jsonl(stdout.getvalue())
    canceled = [event for event in events if event["type"] == "run_canceled"]
    completed = [event for event in events if event["type"] == "run_completed"]
    report_path = tmp_path / ".codecub" / "runs" / "run-cancel-1" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert len(canceled) == 1
    assert canceled[0]["run_id"] == "run-cancel-1"
    assert completed == []
    phases = [
        event["payload"].get("phase")
        for event in events
        if event["type"] == "run_status" and event["run_id"] == "run-cancel-1"
    ]
    assert phases.index("cancel_requested") < phases.index("cancelling") < phases.index("canceled")
    assert not (tmp_path / "canceled.txt").exists()
    assert report["stop_reason"] == "user_canceled"
    assert report["task_state"]["run_id"] == "run-cancel-1"


def test_app_runner_appends_a_second_turn_without_busy_error(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","run_id":"run-a","message":"first"}\n'),
            ("", '{"type":"send_message","run_id":"run-b","message":"second","busy_policy":"APPEND"}\n'),
            ('"run_id":"run-b","payload":{"final"', '{"type":"close"}\n'),
        ],
    )

    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        return Pico(
            model_client=SlowNonStreamingFakeModelClient(["<final>first done</final>", "<final>second done</final>"]),
            workspace=workspace,
            session_store=SessionStore(Path(args.cwd) / ".codecub" / "sessions"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    assert run_app_mode(make_args(tmp_path), stdin=stdin, stdout=stdout, agent_factory=build) == 0
    events = parse_jsonl(stdout.getvalue())
    assert not any("another run is already active" in str(event) for event in events)
    completed = [event["run_id"] for event in events if event["type"] == "run_completed"]
    assert completed == ["run-a", "run-b"]


def test_app_runner_injects_constraint_into_next_model_call(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdout = io.StringIO()
    client = SlowNonStreamingFakeModelClient(
        [
            '<tool name="read_file" path="README.md" start="1" end="1"></tool>',
            "<final>constraint observed</final>",
        ]
    )
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","run_id":"run-a","message":"inspect"}\n'),
            ("", '{"type":"send_message","message":"Do not modify auth.py","busy_policy":"INJECT"}\n'),
            ('"type":"run_completed"', '{"type":"close"}\n'),
        ],
    )

    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        return Pico(
            model_client=client,
            workspace=workspace,
            session_store=SessionStore(Path(args.cwd) / ".codecub" / "sessions"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    assert run_app_mode(make_args(tmp_path), stdin=stdin, stdout=stdout, agent_factory=build) == 0
    assert len(client.prompts) >= 2
    assert "Protected runtime constraints" in client.prompts[1]
    assert "Do not modify auth.py" in client.prompts[1]


def test_app_runner_interrupts_before_running_the_priority_turn(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","run_id":"run-a","message":"first"}\n'),
            ("", '{"type":"send_message","run_id":"run-b","message":"second","busy_policy":"INTERRUPT"}\n'),
            ('"run_id":"run-b","payload":{"final"', '{"type":"close"}\n'),
        ],
    )

    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        return Pico(
            model_client=SlowNonStreamingFakeModelClient(["<final>discarded</final>", "<final>priority done</final>"]),
            workspace=workspace,
            session_store=SessionStore(Path(args.cwd) / ".codecub" / "sessions"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    assert run_app_mode(make_args(tmp_path), stdin=stdin, stdout=stdout, agent_factory=build) == 0
    events = parse_jsonl(stdout.getvalue())
    assert [event["run_id"] for event in events if event["type"] == "run_canceled"] == ["run-a"]
    assert [event["run_id"] for event in events if event["type"] == "run_completed"] == ["run-b"]


def test_app_runner_approves_pending_risky_tool_and_emits_diff_summary(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    args = make_args(tmp_path)
    args.approval = "ask"
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","run_id":"run-1","message":"write"}\n'),
            ('"type":"approval_requested"', '{"type":"approve_operation","run_id":"run-1","approval_id":"approval-1"}\n'),
            ('"type":"run_completed"', '{"type":"close"}\n'),
        ],
    )

    exit_code = run_app_mode(
        args,
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(
            [
                '<tool name="write_file" path="approved.txt"><content>ok\n</content></tool>',
                "<final>done</final>",
            ]
        ),
    )

    events = parse_jsonl(stdout.getvalue())
    tool_results = [event for event in events if event["type"] == "tool_result"]
    diff_events = [event for event in events if event["type"] == "diff_summary"]

    assert exit_code == 0
    assert any(event["type"] == "approval_requested" for event in events)
    assert any(event["type"] == "approval_resolved" and event["payload"]["decision"] == "approved" for event in events)
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "ok\n"
    assert any("approved.txt" in path for event in tool_results for path in event["payload"].get("affected_paths", []))
    assert any(event["payload"].get("workspace_changed") is True for event in tool_results)
    assert any("approved.txt" in path for event in diff_events for path in event["payload"].get("affected_paths", []))


def test_app_runner_rejects_pending_risky_tool_without_mutation(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    args = make_args(tmp_path)
    args.approval = "ask"
    stdout = io.StringIO()
    stdin = ApprovalAwareStdin(
        stdout,
        [
            ("", '{"type":"send_message","run_id":"run-1","message":"write"}\n'),
            ('"type":"approval_requested"', '{"type":"reject_operation","run_id":"run-1","approval_id":"approval-1","reason":"too risky"}\n'),
            ('"type":"run_completed"', '{"type":"close"}\n'),
        ],
    )

    exit_code = run_app_mode(
        args,
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(
            [
                '<tool name="write_file" path="rejected.txt"><content>no\n</content></tool>',
                "<final>handled rejection</final>",
            ]
        ),
    )

    events = parse_jsonl(stdout.getvalue())

    assert exit_code == 0
    assert any(event["type"] == "approval_requested" for event in events)
    assert any(event["type"] == "approval_resolved" and event["payload"]["decision"] == "rejected" for event in events)
    assert not (tmp_path / "rejected.txt").exists()


def test_app_runner_detects_and_imports_legacy_pico_after_command(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    legacy = tmp_path / ".pico" / "sessions"
    legacy.mkdir(parents=True)
    legacy_source = legacy / "old.json"
    legacy_source.write_text('{"id":"old","history":[]}', encoding="utf-8")
    stdin = io.StringIO('{"type":"import_legacy_pico"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory([]),
    )

    events = parse_jsonl(stdout.getvalue())

    assert exit_code == 0
    assert any(event["type"] == "legacy_import_detected" and event["payload"]["session_count"] == 1 for event in events)
    assert any(event["type"] == "legacy_import_completed" and event["payload"]["imported_count"] == 1 for event in events)
    assert legacy_source.read_text(encoding="utf-8") == '{"id":"old","history":[]}'
    assert (tmp_path / ".codecub" / "sessions" / "old.json").exists()
