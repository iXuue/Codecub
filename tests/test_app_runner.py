import argparse
import io
import json
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
    stdin = io.StringIO('{"type":"send_message","message":"say hello"}\n{"type":"close"}\n')
    stdout = io.StringIO()

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


def test_app_runner_emits_single_compatibility_delta_when_runtime_did_not_stream(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO('{"type":"send_message","message":"say hello"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(["<final>Hello from compatibility.</final>"]),
    )

    events = parse_jsonl(stdout.getvalue())
    deltas = [event for event in events if event["type"] == "assistant_delta"]

    assert exit_code == 0
    assert len(deltas) == 1
    assert deltas[0]["payload"]["text"] == "Hello from compatibility."


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
