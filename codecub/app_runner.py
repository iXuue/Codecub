import itertools
import sys
import threading
from datetime import datetime

from .app_protocol import encode_event, make_event, parse_command_line
from .legacy_import import detect_legacy_pico, import_legacy_pico_sessions


class ApprovalRequest:
    def __init__(self, approval_id, run_id, tool_name, args):
        self.approval_id = approval_id
        self.run_id = run_id
        self.tool_name = tool_name
        self.args = dict(args or {})
        self.event = threading.Event()
        self.approved = False
        self.reason = ""


def _new_run_id():
    return "run_" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _write_event(stdout, event_type, session_id="", run_id="", payload=None):
    event = make_event(event_type, session_id=session_id, run_id=run_id, payload=payload or {})
    stdout.write(encode_event(event))
    stdout.flush()
    return event


def _run_artifact_payload(agent):
    run_dir = getattr(agent, "current_run_dir", None)
    if not run_dir:
        return {"run_dir": "", "trace_path": "", "report_path": ""}
    return {
        "run_dir": str(run_dir),
        "trace_path": str(run_dir / "trace.jsonl"),
        "report_path": str(run_dir / "report.json"),
    }


def _tool_result_payload(payload):
    return {
        "tool_name": payload.get("name", ""),
        "args": payload.get("args", {}),
        "result": payload.get("result", ""),
        "status": payload.get("tool_status", ""),
        "code": payload.get("tool_error_code", ""),
        "affected_paths": payload.get("affected_paths", []),
        "workspace_changed": payload.get("workspace_changed", False),
        "diff_summary": payload.get("diff_summary", []),
        "duration_ms": payload.get("duration_ms", 0),
        "risk_level": payload.get("risk_level", ""),
        "read_only": payload.get("read_only", False),
    }


def run_app_mode(args, stdin=None, stdout=None, agent_factory=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if agent_factory is None:
        from .cli import build_agent

        agent_factory = build_agent
    agent = agent_factory(args)
    session_id = agent.session.get("id", "")

    output_lock = threading.Lock()
    pending_lock = threading.Lock()
    pending_approvals = {}
    approval_counter = itertools.count(1)
    active_run = {"run_id": "", "thread": None, "cancel_requested": False}
    canceled_run_ids = set()

    def emit(event_type, run_id="", payload=None):
        with output_lock:
            return _write_event(stdout, event_type, session_id=session_id, run_id=run_id, payload=payload or {})

    def reject_pending_for_run(run_id, reason):
        with pending_lock:
            requests = [
                request for request in pending_approvals.values()
                if not run_id or request.run_id == run_id
            ]
            for request in requests:
                request.approved = False
                request.reason = reason
                request.event.set()

    def approval_handler(name, approval_args, runtime):
        run_id = active_run.get("run_id", "")
        approval_id = f"approval-{next(approval_counter)}"
        request = ApprovalRequest(approval_id, run_id, name, approval_args)
        with pending_lock:
            pending_approvals[approval_id] = request
        emit(
            "approval_requested",
            run_id=run_id,
            payload={
                "approval_id": approval_id,
                "tool_name": name,
                "args": dict(approval_args or {}),
                "cwd": str(runtime.root),
                "timeout": dict(approval_args or {}).get("timeout"),
                "diff_summary": [],
                "risk_level": "high",
            },
        )
        request.event.wait()
        decision = "approved" if request.approved else "rejected"
        emit(
            "approval_resolved",
            run_id=run_id,
            payload={
                "approval_id": approval_id,
                "tool_name": name,
                "decision": decision,
                "reason": request.reason,
            },
        )
        with pending_lock:
            pending_approvals.pop(approval_id, None)
        return request.approved

    def event_handler(event_name, payload, runtime, task_state):
        if event_name != "tool_executed":
            return
        run_id = getattr(task_state, "run_id", "") or active_run.get("run_id", "")
        tool_payload = _tool_result_payload(payload)
        emit("tool_result", run_id=run_id, payload=tool_payload)
        if tool_payload["workspace_changed"] or tool_payload["diff_summary"]:
            emit(
                "diff_summary",
                run_id=run_id,
                payload={
                    "tool_name": tool_payload["tool_name"],
                    "affected_paths": tool_payload["affected_paths"],
                    "workspace_changed": tool_payload["workspace_changed"],
                    "diff_summary": tool_payload["diff_summary"],
                },
            )

    agent.approval_handler = approval_handler
    agent.event_handler = event_handler

    emit(
        "session_started",
        payload={
            "cwd": str(agent.root),
            "approval_policy": agent.approval_policy,
            "session_path": str(agent.session_path),
        },
    )
    legacy = detect_legacy_pico(agent.root)
    if legacy["exists"] and legacy["session_count"] > 0:
        emit(
            "legacy_import_detected",
            payload={
                "exists": True,
                "session_count": legacy["session_count"],
                "session_paths": legacy["session_paths"],
            },
        )

    def run_worker(run_id, message):
        try:
            answer = agent.ask(message)
        except Exception as exc:
            if run_id in canceled_run_ids:
                return
            payload = {
                "error_type": exc.__class__.__name__,
                "message": str(exc),
                **_run_artifact_payload(agent),
            }
            emit("run_failed", run_id=run_id, payload=payload)
            return
        finally:
            pass

        if run_id in canceled_run_ids or active_run.get("cancel_requested"):
            return

        emit("assistant_delta", run_id=run_id, payload={"text": answer})
        emit("assistant_message", run_id=run_id, payload={"text": answer})
        emit("run_completed", run_id=run_id, payload={"final": answer, **_run_artifact_payload(agent)})
        if active_run.get("run_id") == run_id:
            active_run["run_id"] = ""
            active_run["thread"] = None
            active_run["cancel_requested"] = False

    def resolve_approval(command):
        approval_id = command.get("approval_id", "")
        run_id = command.get("run_id", "")
        with pending_lock:
            request = pending_approvals.get(approval_id)
        if request is None:
            emit(
                "tool_result",
                run_id=run_id,
                payload={
                    "status": "error",
                    "code": "unknown_approval",
                    "approval_id": approval_id,
                    "command_type": command["type"],
                },
            )
            return
        if run_id and request.run_id and run_id != request.run_id:
            emit(
                "tool_result",
                run_id=run_id,
                payload={
                    "status": "error",
                    "code": "run_mismatch",
                    "approval_id": approval_id,
                    "command_type": command["type"],
                },
            )
            return
        request.approved = command["type"] == "approve_operation"
        request.reason = command.get("reason", "")
        request.event.set()

    for raw_line in stdin:
        if not str(raw_line).strip():
            continue
        try:
            command = parse_command_line(raw_line)
        except ValueError as exc:
            emit(
                "run_failed",
                payload={
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
            continue

        command_type = command["type"]
        if command_type == "close":
            reject_pending_for_run("", "session_closed")
            thread = active_run.get("thread")
            if thread is not None:
                thread.join(timeout=2)
            emit("session_closed")
            return 0

        if command_type == "cancel_run":
            run_id = command.get("run_id", "") or active_run.get("run_id", "")
            if run_id:
                canceled_run_ids.add(run_id)
            active_run["cancel_requested"] = True
            reject_pending_for_run(run_id, "user_requested")
            emit("run_canceled", run_id=run_id, payload={"reason": "user_requested", **_run_artifact_payload(agent)})
            continue

        if command_type in {"approve_operation", "reject_operation"}:
            resolve_approval(command)
            continue

        if command_type == "import_legacy_pico":
            try:
                summary = import_legacy_pico_sessions(agent.root)
            except Exception as exc:
                emit(
                    "legacy_import_failed",
                    payload={
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                )
                continue
            emit("legacy_import_completed", payload=summary)
            continue

        if command_type == "send_message":
            current_thread = active_run.get("thread")
            if current_thread is not None and current_thread.is_alive():
                emit(
                    "run_failed",
                    run_id=active_run.get("run_id", ""),
                    payload={
                        "error_type": "RuntimeError",
                        "message": "another run is already active",
                    },
                )
                continue
            run_id = command.get("run_id") or _new_run_id()
            message = command["message"]
            active_run["run_id"] = run_id
            active_run["cancel_requested"] = False
            emit("user_message_received", run_id=run_id, payload={"message": message})
            thread = threading.Thread(target=run_worker, args=(run_id, message), daemon=True)
            active_run["thread"] = thread
            thread.start()
            continue

    thread = active_run.get("thread")
    if thread is not None:
        thread.join(timeout=2)
    return 0
