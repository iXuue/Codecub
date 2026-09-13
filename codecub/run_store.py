"""运行工件落盘。

session.json 负责保存“可恢复的会话状态”；RunStore 负责保存“单次运行的审计工件”，
例如 task_state、trace 和 report。两者分开后，恢复现场和复盘证据不会混在一起。
"""

import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import portalocker


def _run_id(value):
    if hasattr(value, "run_id"):
        return value.run_id
    return str(value)


class RunStore:
    _side_effect_locks = {}
    _side_effect_locks_guard = threading.Lock()
    _RUN_LOCK_TIMEOUT_SECONDS = 60

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id):
        return self.root / _run_id(run_id)

    def task_state_path(self, run_id):
        return self.run_dir(run_id) / "task_state.json"

    def trace_path(self, run_id):
        return self.run_dir(run_id) / "trace.jsonl"

    def report_path(self, run_id):
        return self.run_dir(run_id) / "report.json"

    def usage_path(self, run_id):
        return self.run_dir(run_id) / "usage.jsonl"

    def start_run(self, task_state):
        # 每次 ask() 都会生成一个 run 目录。
        # 这样一次用户请求对应一组独立工件，后续排查更容易。
        run_dir = self.run_dir(task_state)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.write_task_state(task_state)
        return run_dir

    def write_task_state(self, task_state):
        path = self.task_state_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._run_lock(_run_id(task_state)):
            payload = task_state.to_dict()
            if path.exists():
                persisted = self._load_task_state_payload(task_state)
                for field in ("side_effect_operations", "legacy_operation_identities"):
                    if field in persisted:
                        payload[field] = dict(persisted.get(field) or {})
            self._write_json_atomic(path, payload)
        return path

    def append_trace(self, task_state, event):
        path = self.trace_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        # trace 采用 jsonl 追加写入，原因是 agent 运行过程是流式事件序列，
        # 逐条落盘比“最后一次性写整份 trace”更稳，也更适合调试。
        with self._run_lock(_run_id(task_state)):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True))
                handle.write("\n")
                handle.flush()
        return path

    def append_usage(self, task_state, usage_record):
        path = self.usage_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._run_lock(_run_id(task_state)):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(usage_record, sort_keys=True, ensure_ascii=True))
                handle.write("\n")
                handle.flush()
        return path

    def write_report(self, task_state, report):
        path = self.report_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(path, report)
        return path

    def load_task_state(self, task_id):
        return json.loads(self.task_state_path(task_id).read_text(encoding="utf-8"))

    def load_report(self, task_id):
        return json.loads(self.report_path(task_id).read_text(encoding="utf-8"))

    def load_usage(self, run_id):
        path = self.usage_path(run_id)
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    @classmethod
    def _side_effect_lock(cls, run_id):
        key = str(_run_id(run_id))
        with cls._side_effect_locks_guard:
            return cls._side_effect_locks.setdefault(key, threading.Lock())

    @contextmanager
    def _run_lock(self, run_id):
        """Serialize all mutable artifacts for one Run in threads and processes."""
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = run_dir / ".run_store.lock"
        with self._side_effect_lock(run_id):
            with portalocker.Lock(
                str(lock_path),
                mode="a+",
                timeout=self._RUN_LOCK_TIMEOUT_SECONDS,
            ):
                yield

    @staticmethod
    def _side_effect_timestamp():
        return datetime.now(timezone.utc).isoformat()

    def _load_task_state_payload(self, task_state):
        path = self.task_state_path(task_state)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return task_state.to_dict()

    def claim_side_effect_operation(self, task_state, operation_key, tool_name, args_digest):
        """Atomically claim one run-scoped logical side effect.

        A lingering claimed record is intentionally not replayed: after a crash
        we cannot prove whether the downstream side effect committed.
        """
        key = str(operation_key or "").strip()
        if not key:
            raise ValueError("operation_key must not be empty")
        with self._run_lock(task_state.run_id):
            payload = self._load_task_state_payload(task_state)
            ledger = dict(payload.get("side_effect_operations", {}) or {})
            previous = ledger.get(key)
            if previous is not None:
                same_identity = (
                    previous.get("tool_name") == str(tool_name)
                    and previous.get("args_digest") == str(args_digest)
                )
                task_state.side_effect_operations = ledger
                return {
                    "claimed": False,
                    "conflict": not same_identity,
                    "prior_state": str(previous.get("state", "")),
                    "entry": dict(previous),
                }
            timestamp = self._side_effect_timestamp()
            entry = {
                "operation_key": key,
                "tool_name": str(tool_name),
                "args_digest": str(args_digest),
                "state": "claimed",
                "created_at": timestamp,
                "updated_at": timestamp,
                "result_metadata": {},
            }
            ledger[key] = entry
            payload["side_effect_operations"] = ledger
            self._write_json_atomic(self.task_state_path(task_state), payload)
            task_state.side_effect_operations = ledger
            return {"claimed": True, "conflict": False, "prior_state": "", "entry": entry}

    def update_side_effect_operation(self, task_state, operation_key, state, result_metadata=None):
        """Persist a terminal/uncertain outcome without exposing raw payloads."""
        key = str(operation_key or "").strip()
        with self._run_lock(task_state.run_id):
            payload = self._load_task_state_payload(task_state)
            ledger = dict(payload.get("side_effect_operations", {}) or {})
            entry = dict(ledger.get(key, {}))
            if not entry:
                raise ValueError("side effect operation was not claimed")
            entry["state"] = str(state)
            entry["updated_at"] = self._side_effect_timestamp()
            entry["result_metadata"] = dict(result_metadata or {})
            ledger[key] = entry
            payload["side_effect_operations"] = ledger
            self._write_json_atomic(self.task_state_path(task_state), payload)
            task_state.side_effect_operations = ledger
            return entry

    def ensure_legacy_operation_identity(
        self, task_state, assistant_attempt, recovered_ordinal, tool_name
    ):
        """Persist and reuse a deterministic identity for a recovered call."""
        identity = (
            f"assistant-attempt:{int(assistant_attempt)}:"
            f"recovered-ordinal:{int(recovered_ordinal)}"
        )
        candidate = (
            f"legacy:{_run_id(task_state.run_id)}:{identity}:"
            f"tool:{str(tool_name or '').strip()}"
        )
        with self._run_lock(task_state.run_id):
            payload = self._load_task_state_payload(task_state)
            identities = dict(payload.get("legacy_operation_identities", {}) or {})
            operation_key = str(identities.get(identity) or candidate)
            if identities.get(identity) != operation_key:
                identities[identity] = operation_key
                payload["legacy_operation_identities"] = identities
                self._write_json_atomic(self.task_state_path(task_state), payload)
            task_state.legacy_operation_identities = identities
            return {"identity": identity, "operation_key": operation_key}

    def _write_json_atomic(self, path, payload):
        # 原子写：先写临时文件，再 replace。
        # 这样即使中途异常，也不容易留下半截 JSON。
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_name = handle.name
        Path(temp_name).replace(path)
