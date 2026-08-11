import json
from datetime import datetime, timezone

SUPPORTED_COMMAND_TYPES = {
    "send_message",
    "approve_operation",
    "reject_operation",
    "cancel_run",
    "import_legacy_pico",
    "close",
}

SUPPORTED_EVENT_TYPES = {
    "session_started",
    "session_closed",
    "user_message_received",
    "run_status",
    "assistant_delta",
    "assistant_message",
    "run_completed",
    "run_failed",
    "run_canceled",
    "usage_updated",
    "usage_snapshot",
    "tool_result",
    "approval_requested",
    "approval_resolved",
    "diff_summary",
    "legacy_import_detected",
    "legacy_import_completed",
    "legacy_import_failed",
}


def now_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_event(event_type, session_id="", run_id="", payload=None):
    event_type = str(event_type).strip()
    if not event_type:
        raise ValueError("event type must not be empty")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(f"unknown event type: {event_type}")
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
    return json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"


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
