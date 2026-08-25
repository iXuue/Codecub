import json

import pytest

from codecub.app_protocol import encode_event, make_event, parse_command_line


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


def test_make_event_accepts_approval_diff_and_import_event_types():
    approval = make_event(
        "approval_requested",
        session_id="session-1",
        run_id="run-1",
        payload={"approval_id": "approval-1", "tool_name": "write_file"},
    )
    diff = make_event(
        "diff_summary",
        session_id="session-1",
        run_id="run-1",
        payload={"affected_paths": ["README.md"]},
    )
    imported = make_event(
        "legacy_import_completed",
        session_id="session-1",
        payload={"imported_count": 2, "skipped_count": 1},
    )

    assert approval["type"] == "approval_requested"
    assert diff["type"] == "diff_summary"
    assert imported["type"] == "legacy_import_completed"


def test_make_event_accepts_run_status():
    event = make_event(
        "run_status",
        session_id="session-1",
        run_id="run-1",
        payload={
            "phase": "model_streaming",
            "label": "Receiving model response",
            "elapsed_ms": 42,
        },
    )

    assert event["type"] == "run_status"
    assert event["payload"]["phase"] == "model_streaming"


def test_make_event_accepts_usage_events():
    updated = make_event("usage_updated", session_id="session-1", run_id="run-1", payload={"groups": []})
    snapshot = make_event("usage_snapshot", session_id="session-1", payload={"scope": "session", "groups": []})

    assert updated["type"] == "usage_updated"
    assert snapshot["type"] == "usage_snapshot"


def test_make_event_rejects_unknown_event_type():
    with pytest.raises(ValueError, match="unknown event type"):
        make_event("unknown_event")


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


def test_encode_event_escapes_unicode_for_ascii_safe_transport():
    line = encode_event(
        {
            "type": "user_message_received",
            "timestamp": "2026-06-11T00:00:00Z",
            "session_id": "session-1",
            "run_id": "run-1",
            "payload": {"message": "查看我的代码"},
        }
    )

    assert "查看我的代码" not in line
    assert line.encode("ascii")
    assert json.loads(line)["payload"]["message"] == "查看我的代码"


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


def test_parse_import_legacy_pico_command():
    command = parse_command_line('{"type":"import_legacy_pico","session_id":"session-1"}')

    assert command["type"] == "import_legacy_pico"
    assert command["session_id"] == "session-1"


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
