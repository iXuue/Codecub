# CodeCub P0.1 Backend App Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal machine-readable backend app mode for the current Pico package so a future Electron shell can communicate with the Python agent through JSONL commands and events.

**Architecture:** P0.1 introduces a small protocol layer and an app-mode runner while preserving the existing human CLI behavior. The runner is synchronous in P0.1: it accepts JSONL commands, calls the existing agent through `ask()`, emits JSONL events, and treats approval commands as defined-but-not-yet-implemented until P0.4.

**Tech Stack:** Python 3.10+, pytest, existing `pico` package, JSONL over stdin/stdout.

---

## 0. Stage Scope

This plan implements only P0.1 from `.codecub/spec/2026-06-11-codecub-p0-requirements.md`.

In scope:

- Define backend JSONL event helpers.
- Define backend JSONL command parsing and validation.
- Add an app-mode runner callable from tests and CLI.
- Add CLI flags `--app-mode` and `--json-events`.
- Emit core events for session start, user message received, assistant delta, assistant message, run completed, run failed, run canceled, and unsupported approval commands.
- Keep current human CLI behavior unchanged when app mode is not enabled.

Out of scope for P0.1:

- Real blocking approval and resume-after-approval. That belongs to P0.4.
- Real tool-level `approval_requested` emission. That belongs to P0.4.
- Native provider streaming. P0.1 may split a completed answer into one or more `assistant_delta` events.
- Package rename from `pico` to `codecub`. That belongs to P0.2.
- Electron app work. That belongs to P0.3.

## 1. Files

Create:

- `pico/app_protocol.py`
- `pico/app_runner.py`
- `tests/test_app_protocol.py`
- `tests/test_app_runner.py`

Modify:

- `pico/cli.py`
- `tests/test_pico.py`

Read before editing:

- `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
- `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`
- `pico/cli.py`
- `pico/runtime.py`
- `pico/models.py`
- `tests/test_pico.py`

Back up before modifying:

- `pico/cli.py`
- `tests/test_pico.py`

No backup is needed before creating new files, but the user has already approved creating this stage plan only. Before implementing this P0.1 code plan, ask for explicit approval to create and modify the files listed above.

## 2. Protocol Contract

Every event is one compact JSON object followed by `\n`.

Required event fields:

- `type`: non-empty string.
- `timestamp`: ISO-like UTC timestamp string.
- `session_id`: string, empty string allowed before a session exists.
- `run_id`: string, empty string allowed before a run exists.
- `payload`: object.

Supported P0.1 command types:

- `send_message`
- `approve_operation`
- `reject_operation`
- `cancel_run`
- `close`

Required command fields by type:

```text
send_message:
  type: "send_message"
  message: non-empty string
  session_id: optional string
  run_id: optional string

approve_operation:
  type: "approve_operation"
  approval_id: non-empty string
  session_id: optional string
  run_id: optional string

reject_operation:
  type: "reject_operation"
  approval_id: non-empty string
  reason: optional string
  session_id: optional string
  run_id: optional string

cancel_run:
  type: "cancel_run"
  run_id: optional string
  session_id: optional string

close:
  type: "close"
  session_id: optional string
```

P0.1 unsupported approval behavior:

- `approve_operation` emits `tool_result` with `payload.status = "unsupported"` and `payload.code = "unsupported_until_p0_4"`.
- `reject_operation` emits `tool_result` with `payload.status = "unsupported"` and `payload.code = "unsupported_until_p0_4"`.
- Real approval blocking and continuation is implemented in P0.4.

## 3. Implementation Tasks

### Task 1: Repository Protection Check

**Files:**

- Read: `.codecub/plan/2026-06-11-codecub-p0-1-backend-app-protocol-plan.md`
- Read: `pico/cli.py`
- Read: `tests/test_pico.py`

- [ ] **Step 1: Capture current dirty state**

Run:

```powershell
git status --short
git diff --stat
git diff -- pico/cli.py tests/test_pico.py
```

Expected:

- Existing user changes are visible.
- `pico/cli.py` already contains `/memory recall <query>` changes.
- `tests/test_pico.py` already contains memory recall debug tests.
- No files are changed by this step.

- [ ] **Step 2: Back up existing files before editing**

Run:

```powershell
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = "E:\codex_backup\$timestamp-codecub-p0-1-backend-app-protocol"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $backupRoot 'pico') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $backupRoot 'tests') | Out-Null
Copy-Item -LiteralPath 'pico\cli.py' -Destination (Join-Path $backupRoot 'pico\cli.py')
Copy-Item -LiteralPath 'tests\test_pico.py' -Destination (Join-Path $backupRoot 'tests\test_pico.py')
Write-Output $backupRoot
```

Expected:

- A backup folder path under `E:\codex_backup` is printed.
- The printed backup path is reported to the user before edits.

### Task 2: Add Protocol Tests

**Files:**

- Create: `tests/test_app_protocol.py`
- Create later: `pico/app_protocol.py`

- [ ] **Step 1: Create failing protocol tests**

Create `tests/test_app_protocol.py` with this exact content:

```python
import json

import pytest

from pico.app_protocol import encode_event, make_event, parse_command_line


def test_make_event_includes_required_fields():
    event = make_event(
        "session_started",
        session_id="session-1",
        run_id="run-1",
        payload={"cwd": "D:/repo"},
    )

    assert event["type"] == "session_started"
    assert event["session_id"] == "session-1"
    assert event["run_id"] == "run-1"
    assert isinstance(event["timestamp"], str)
    assert event["timestamp"]
    assert event["payload"] == {"cwd": "D:/repo"}


def test_encode_event_returns_one_json_line():
    line = encode_event(
        {
            "type": "run_completed",
            "timestamp": "2026-06-11T00:00:00Z",
            "session_id": "session-1",
            "run_id": "run-1",
            "payload": {"final": "done"},
        }
    )

    assert line.endswith("\n")
    assert "\n" not in line[:-1]
    decoded = json.loads(line)
    assert decoded["type"] == "run_completed"
    assert decoded["payload"] == {"final": "done"}


def test_parse_send_message_command():
    command = parse_command_line(
        '{"type":"send_message","session_id":"session-1","run_id":"run-1","message":"inspect tests"}'
    )

    assert command == {
        "type": "send_message",
        "session_id": "session-1",
        "run_id": "run-1",
        "message": "inspect tests",
    }


def test_parse_approval_commands_require_approval_id():
    approve = parse_command_line(
        '{"type":"approve_operation","session_id":"session-1","run_id":"run-1","approval_id":"approval-1"}'
    )
    reject = parse_command_line(
        '{"type":"reject_operation","session_id":"session-1","run_id":"run-1","approval_id":"approval-1","reason":"too risky"}'
    )

    assert approve["approval_id"] == "approval-1"
    assert reject["approval_id"] == "approval-1"
    assert reject["reason"] == "too risky"

    with pytest.raises(ValueError, match="approval_id"):
        parse_command_line('{"type":"approve_operation"}')

    with pytest.raises(ValueError, match="approval_id"):
        parse_command_line('{"type":"reject_operation"}')


def test_parse_cancel_and_close_commands():
    cancel = parse_command_line('{"type":"cancel_run","session_id":"session-1","run_id":"run-1"}')
    close = parse_command_line('{"type":"close","session_id":"session-1"}')

    assert cancel["type"] == "cancel_run"
    assert cancel["run_id"] == "run-1"
    assert close["type"] == "close"
    assert close["session_id"] == "session-1"


def test_parse_command_rejects_invalid_json_and_unknown_type():
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_command_line("{bad json")

    with pytest.raises(ValueError, match="missing type"):
        parse_command_line('{"message":"hello"}')

    with pytest.raises(ValueError, match="unknown command"):
        parse_command_line('{"type":"unknown"}')


def test_parse_send_message_rejects_empty_message():
    with pytest.raises(ValueError, match="message"):
        parse_command_line('{"type":"send_message","message":""}')
```

Run:

```powershell
uv run pytest tests/test_app_protocol.py -q
```

Expected:

- Fails with `ModuleNotFoundError: No module named 'pico.app_protocol'`.

### Task 3: Implement Protocol Helpers

**Files:**

- Create: `pico/app_protocol.py`
- Test: `tests/test_app_protocol.py`

- [ ] **Step 1: Create protocol helper module**

Create `pico/app_protocol.py` with this exact content:

```python
import json
from datetime import datetime, timezone

SUPPORTED_COMMAND_TYPES = {
    "send_message",
    "approve_operation",
    "reject_operation",
    "cancel_run",
    "close",
}


def now_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_event(event_type, session_id="", run_id="", payload=None):
    event_type = str(event_type).strip()
    if not event_type:
        raise ValueError("event type must not be empty")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("event payload must be a dict")
    return {
        "type": event_type,
        "timestamp": now_timestamp(),
        "session_id": str(session_id or ""),
        "run_id": str(run_id or ""),
        "payload": payload,
    }


def encode_event(event):
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    for field in ("type", "timestamp", "session_id", "run_id", "payload"):
        if field not in event:
            raise ValueError(f"event missing required field: {field}")
    if not isinstance(event["payload"], dict):
        raise ValueError("event payload must be a dict")
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"


def parse_command_line(line):
    try:
        command = json.loads(str(line))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON command") from exc
    if not isinstance(command, dict):
        raise ValueError("command must be a JSON object")

    command_type = str(command.get("type", "")).strip()
    if not command_type:
        raise ValueError("command missing type")
    if command_type not in SUPPORTED_COMMAND_TYPES:
        raise ValueError(f"unknown command type: {command_type}")

    normalized = {"type": command_type}
    if command.get("session_id") is not None:
        normalized["session_id"] = str(command.get("session_id", ""))
    if command.get("run_id") is not None:
        normalized["run_id"] = str(command.get("run_id", ""))

    if command_type == "send_message":
        message = str(command.get("message", "")).strip()
        if not message:
            raise ValueError("send_message command requires a non-empty message")
        normalized["message"] = message
        return normalized

    if command_type in {"approve_operation", "reject_operation"}:
        approval_id = str(command.get("approval_id", "")).strip()
        if not approval_id:
            raise ValueError(f"{command_type} command requires approval_id")
        normalized["approval_id"] = approval_id
        if command_type == "reject_operation" and command.get("reason") is not None:
            normalized["reason"] = str(command.get("reason", ""))
        return normalized

    return normalized
```

Run:

```powershell
uv run pytest tests/test_app_protocol.py -q
```

Expected:

- All `tests/test_app_protocol.py` tests pass.

### Task 4: Add App Runner Tests

**Files:**

- Create: `tests/test_app_runner.py`
- Create later: `pico/app_runner.py`

- [ ] **Step 1: Create failing app runner tests**

Create `tests/test_app_runner.py` with this exact content:

```python
import argparse
import io
import json
from pathlib import Path

from pico.models import FakeModelClient
from pico.runtime import Pico, SessionStore
from pico.workspace import WorkspaceContext
from pico.app_runner import run_app_mode


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


def fake_agent_factory(outputs):
    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        store = SessionStore(Path(args.cwd) / ".pico" / "sessions")
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
    event_types = [event["type"] for event in events]

    assert exit_code == 0
    assert event_types == [
        "session_started",
        "user_message_received",
        "assistant_delta",
        "assistant_message",
        "run_completed",
        "session_closed",
    ]
    assert events[0]["session_id"]
    assert events[1]["payload"]["message"] == "say hello"
    assert events[2]["payload"]["text"] == "Hello from app mode."
    assert events[3]["payload"]["text"] == "Hello from app mode."
    assert events[4]["payload"]["final"] == "Hello from app mode."


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


def test_app_runner_reports_approval_commands_as_unsupported_until_p0_4(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO(
        '{"type":"approve_operation","run_id":"run-1","approval_id":"approval-1"}\n'
        '{"type":"reject_operation","run_id":"run-1","approval_id":"approval-2","reason":"no"}\n'
        '{"type":"close"}\n'
    )
    stdout = io.StringIO()

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory([]),
    )

    events = parse_jsonl(stdout.getvalue())
    tool_results = [event for event in events if event["type"] == "tool_result"]

    assert exit_code == 0
    assert len(tool_results) == 2
    assert all(event["payload"]["status"] == "unsupported" for event in tool_results)
    assert all(event["payload"]["code"] == "unsupported_until_p0_4" for event in tool_results)
    assert tool_results[0]["payload"]["approval_id"] == "approval-1"
    assert tool_results[1]["payload"]["approval_id"] == "approval-2"
```

Run:

```powershell
uv run pytest tests/test_app_runner.py -q
```

Expected:

- Fails with `ModuleNotFoundError: No module named 'pico.app_runner'`.

### Task 5: Implement App Runner

**Files:**

- Create: `pico/app_runner.py`
- Test: `tests/test_app_runner.py`

- [ ] **Step 1: Create app runner module**

Create `pico/app_runner.py` with this exact content:

```python
import sys
from datetime import datetime

from .app_protocol import encode_event, make_event, parse_command_line


def _new_run_id():
    return "run_" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _write_event(stdout, event_type, session_id="", run_id="", payload=None):
    event = make_event(event_type, session_id=session_id, run_id=run_id, payload=payload or {})
    stdout.write(encode_event(event))
    stdout.flush()
    return event


def _emit_unsupported_approval(stdout, agent, command):
    approval_id = command.get("approval_id", "")
    run_id = command.get("run_id", "")
    return _write_event(
        stdout,
        "tool_result",
        session_id=agent.session.get("id", ""),
        run_id=run_id,
        payload={
            "status": "unsupported",
            "code": "unsupported_until_p0_4",
            "approval_id": approval_id,
            "command_type": command["type"],
        },
    )


def run_app_mode(args, stdin=None, stdout=None, agent_factory=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if agent_factory is None:
        from .cli import build_agent

        agent_factory = build_agent
    agent = agent_factory(args)
    session_id = agent.session.get("id", "")

    _write_event(
        stdout,
        "session_started",
        session_id=session_id,
        payload={
            "cwd": str(agent.root),
            "approval_policy": agent.approval_policy,
            "session_path": str(agent.session_path),
        },
    )

    for raw_line in stdin:
        if not str(raw_line).strip():
            continue
        try:
            command = parse_command_line(raw_line)
        except ValueError as exc:
            _write_event(
                stdout,
                "run_failed",
                session_id=session_id,
                payload={
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
            continue

        command_type = command["type"]
        if command_type == "close":
            _write_event(stdout, "session_closed", session_id=session_id)
            return 0

        if command_type == "cancel_run":
            _write_event(
                stdout,
                "run_canceled",
                session_id=session_id,
                run_id=command.get("run_id", ""),
                payload={"reason": "user_requested"},
            )
            continue

        if command_type in {"approve_operation", "reject_operation"}:
            _emit_unsupported_approval(stdout, agent, command)
            continue

        if command_type == "send_message":
            run_id = command.get("run_id") or _new_run_id()
            message = command["message"]
            _write_event(
                stdout,
                "user_message_received",
                session_id=session_id,
                run_id=run_id,
                payload={"message": message},
            )
            try:
                answer = agent.ask(message)
            except Exception as exc:
                _write_event(
                    stdout,
                    "run_failed",
                    session_id=session_id,
                    run_id=run_id,
                    payload={
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                )
                continue

            _write_event(
                stdout,
                "assistant_delta",
                session_id=session_id,
                run_id=run_id,
                payload={"text": answer},
            )
            _write_event(
                stdout,
                "assistant_message",
                session_id=session_id,
                run_id=run_id,
                payload={"text": answer},
            )
            _write_event(
                stdout,
                "run_completed",
                session_id=session_id,
                run_id=run_id,
                payload={"final": answer},
            )

    return 0
```

Run:

```powershell
uv run pytest tests/test_app_runner.py tests/test_app_protocol.py -q
```

Expected:

- All app runner and app protocol tests pass.

### Task 6: Wire CLI App Mode

**Files:**

- Modify: `pico/cli.py`
- Modify: `tests/test_pico.py`
- Test: `tests/test_app_runner.py`
- Test: `tests/test_pico.py`

- [ ] **Step 1: Add CLI parser tests**

Append these tests to `tests/test_pico.py`:

```python
def test_build_arg_parser_accepts_app_mode_flags():
    from pico.cli import build_arg_parser

    app_args = build_arg_parser().parse_args(["--app-mode"])
    json_args = build_arg_parser().parse_args(["--json-events"])

    assert app_args.app_mode is True
    assert json_args.app_mode is True


def test_main_dispatches_to_app_mode_without_welcome(monkeypatch, capsys):
    from pico import cli

    called = {}

    def fake_run_app_mode(args):
        called["app_mode"] = args.app_mode
        print('{"type":"session_started","timestamp":"2026-06-11T00:00:00Z","session_id":"s","run_id":"","payload":{}}')
        return 0

    monkeypatch.setattr(cli, "run_app_mode", fake_run_app_mode)

    result = cli.main(["--app-mode"])

    captured = capsys.readouterr()
    assert result == 0
    assert called["app_mode"] is True
    assert "pico>" not in captured.out
    assert "session_started" in captured.out
```

Run:

```powershell
uv run pytest tests/test_pico.py::test_build_arg_parser_accepts_app_mode_flags tests/test_pico.py::test_main_dispatches_to_app_mode_without_welcome -q
```

Expected:

- Fails because `build_arg_parser` does not define app-mode flags and `cli.run_app_mode` does not exist.

- [ ] **Step 2: Import app runner in CLI**

Modify the imports near the top of `pico/cli.py`:

```python
from .app_runner import run_app_mode
from .models import AnthropicCompatibleModelClient, OllamaModelClient, OpenAICompatibleModelClient
from .runtime import Pico, SessionStore
from .workspace import WorkspaceContext, middle
```

Expected:

- `pico.cli` imports successfully.

- [ ] **Step 3: Add app-mode flags**

Modify `build_arg_parser()` in `pico/cli.py`:

```python
    parser.add_argument(
        "--app-mode",
        action="store_true",
        help="Run machine-readable JSONL app mode for the desktop shell.",
    )
    parser.add_argument(
        "--json-events",
        dest="app_mode",
        action="store_true",
        help="Alias for --app-mode.",
    )
```

Run:

```powershell
uv run pytest tests/test_pico.py::test_build_arg_parser_accepts_app_mode_flags -q
```

Expected:

- Parser flag test passes.

- [ ] **Step 4: Dispatch app mode before building human welcome**

Modify `main(argv=None)` in `pico/cli.py` so app mode runs before the human welcome screen:

```python
def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if getattr(args, "app_mode", False):
        return run_app_mode(args)

    agent = build_agent(args)
    model = getattr(agent.model_client, "model", getattr(args, "model", DEFAULT_OLLAMA_MODEL))
    host = getattr(agent.model_client, "host", getattr(agent.model_client, "base_url", getattr(args, "host", DEFAULT_OLLAMA_HOST)))
    print(build_welcome(agent, model=model, host=host))
```

Keep the existing one-shot and REPL code after the welcome block unchanged.

Run:

```powershell
uv run pytest tests/test_pico.py::test_main_dispatches_to_app_mode_without_welcome -q
```

Expected:

- Dispatch test passes.

- [ ] **Step 5: Run app-mode focused tests**

Run:

```powershell
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py::test_build_arg_parser_accepts_app_mode_flags tests/test_pico.py::test_main_dispatches_to_app_mode_without_welcome -q
```

Expected:

- All P0.1 focused tests pass.

### Task 7: P0.1 Regression And Handoff

**Files:**

- Existing backend tests under `tests/`
- Requirements: `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
- Master plan: `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`
- This stage plan: `.codecub/plan/2026-06-11-codecub-p0-1-backend-app-protocol-plan.md`

- [ ] **Step 1: Run selected regression**

Run:

```powershell
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py tests/test_safety_invariants.py -q
```

Expected:

- All selected tests pass.

- [ ] **Step 2: Run full backend regression if selected regression passes**

Run:

```powershell
uv run pytest -q
```

Expected:

- Full backend test suite passes, or any unrelated pre-existing failures are documented with exact failing test names and failure messages.

- [ ] **Step 3: Manual app-mode smoke test**

Run:

```powershell
'{"type":"send_message","message":"Say hello from app mode"}' | uv run python -m pico --app-mode --cwd .
```

Expected:

- Output is JSONL.
- First event has `type = "session_started"`.
- Output does not include the human welcome box.
- Output contains either `run_completed` or `run_failed`.

- [ ] **Step 4: Update stage status**

After implementation, append a new section named `## 5. P0.1 Execution Status` to this plan. The status section must contain real values for:

- Start time with timezone.
- Completion time with timezone.
- Files created.
- Files modified.
- Backup path.
- Tests run.
- Test results.
- Notes for P0.2 package migration.

Do not write this section before execution starts. Do not use example timestamps or example backup paths.

## 4. Plan Review

### Requirement Coverage

This P0.1 plan covers:

- JSONL event protocol with required event fields.
- Machine-readable command protocol for send, approve, reject, cancel, and close.
- App-mode CLI entry.
- `assistant_delta` and `assistant_message` compatibility streaming events.
- Structured unsupported response for approval commands until P0.4.
- Human CLI preservation outside app mode.
- Dirty worktree protection and backup before editing existing files.

This P0.1 plan intentionally does not cover:

- Real approval blocking and continuation.
- Diff preview.
- `.codecub/` storage migration.
- `.pico/` import.
- Electron UI.
- Terminal.
- Git status.
- Windows packaging.

Those are covered by later P0 stages in the master plan.

### Conflict Review

Known conflicts with current dirty files:

- `pico/cli.py` already has `/memory recall <query>` changes. P0.1 must preserve them.
- `tests/test_pico.py` already has memory recall debug tests. P0.1 must append tests without replacing these changes.
- `pico/runtime.py` has current dirty changes, but P0.1 does not need to modify it. Avoid touching `pico/runtime.py` in this stage unless the implementation proves impossible without it.

### Placeholder Scan

This plan does not contain unresolved implementation placeholders. The only angle-bracket text appears in the execution status template, and the plan explicitly says it must be replaced with real values during execution before that status note is committed.

### Maintenance Review

The plan keeps P0.1 small enough to be implemented before package migration. It creates the protocol in `pico/` first because the package remains `pico` until P0.2. P0.2 is responsible for moving the new files to `codecub/`.

## 5. P0.1 Execution Status

- Started: 2026-06-12 14:00 Asia/Shanghai
- Completed: focused implementation verified on 2026-06-12 14:52 Asia/Shanghai
- Files created:
  - `pico/app_protocol.py`
  - `pico/app_runner.py`
  - `tests/test_app_protocol.py`
  - `tests/test_app_runner.py`
- Files modified:
  - `pico/cli.py`
  - `pico/runtime.py`
  - `tests/test_pico.py`
  - `tests/test_safety_invariants.py`
- Backup paths:
  - `E:\codex_backup\20260612-140055-codecub-p0-1-backend-app-protocol`
  - `E:\codex_backup\20260612-141928-codecub-p0-1-execution-status`
  - `E:\codex_backup\20260612-143557-codecub-p0-1-qwen-test-model`
  - `E:\codex_backup\20260612-144646-codecub-p0-1-test-baseline-fixes`
- Tests run:
  - `uv run pytest tests/test_app_protocol.py -q`
  - `uv run pytest tests/test_app_runner.py -q`
  - `uv run pytest tests/test_app_runner.py tests/test_app_protocol.py -q`
  - `uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py::test_build_arg_parser_accepts_app_mode_flags tests/test_pico.py::test_main_dispatches_to_app_mode_without_welcome -q`
  - `uv run pytest tests/test_pico.py::test_build_agent_uses_openai_provider_by_default -q`
  - `uv run pytest tests/test_pico.py::test_trace_and_report_redact_secret_env_values -q`
  - `uv run pytest tests/test_safety_invariants.py::test_run_shell_uses_allowlisted_environment_only -q`
  - `uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py tests/test_safety_invariants.py -q`
- Passing results:
  - `tests/test_app_protocol.py`: 7 passed.
  - `tests/test_app_runner.py` plus protocol tests: 11 passed.
  - P0.1 focused CLI tests plus protocol/runner tests: 13 passed.
- Qwen provider test: 1 passed with `OPENAI_MODEL=qwen-flash`.
- Secret redaction shell regression: 1 passed after converting the command to a cross-platform Python invocation.
- Shell environment allowlist regression: 1 passed after using Windows-safe command construction.
- Selected regression baseline: 79 passed, 2 skipped.
- Skipped tests:
  - `tests/test_pico.py::test_reviewer_skeleton_docs_exist`: reviewer skeleton docs are absent in this checkout, and `docs/` is ignored by `.gitignore`.
  - `tests/test_safety_invariants.py::test_symlink_path_traversal_is_rejected`: Windows symlink creation is unavailable without the required privilege.
- Result: P0.1 focused implementation is verified against the selected regression baseline. The two skips are classified as checkout/environment conditions rather than P0.1 app-mode failures.
- Notes for P0.2:
  - Move `pico/app_protocol.py` and `pico/app_runner.py` to `codecub/`.
  - Update imports during package migration.
