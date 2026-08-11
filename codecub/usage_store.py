import json
import tempfile
from pathlib import Path

from .telemetry.contracts import build_usage_snapshot, safe_usage_record


class UsageStore:
    def __init__(self, root):
        self.root = Path(root)
        self.sessions_root = self.root / "sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)

    def record(self, usage_record):
        record = safe_usage_record(usage_record)
        session_id = self._safe_id(record.get("session_id"))
        if not session_id or not record.get("usage_id"):
            return None
        rows = self.load_records(session_id)
        if any(row.get("usage_id") == record.get("usage_id") for row in rows):
            return {"inserted": False, "snapshot": self.load_snapshot(session_id)}
        rows.append(record)
        snapshot = build_usage_snapshot(rows, "session", session_id=session_id)
        self._write_json_atomic(self._records_path(session_id), rows)
        self._write_json_atomic(self._summary_path(session_id), snapshot)
        return {"inserted": True, "snapshot": snapshot}

    def load_records(self, session_id):
        safe_id = self._safe_id(session_id)
        if not safe_id:
            return []
        path = self._records_path(safe_id)
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def load_snapshot(self, session_id):
        safe_id = self._safe_id(session_id)
        if not safe_id:
            return build_usage_snapshot([], "session")
        rows = self.load_records(safe_id)
        path = self._summary_path(safe_id)
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                revision = len({str(row.get("usage_id")) for row in rows if row.get("usage_id")})
                if isinstance(value, dict) and value.get("schema_version") == 2 and value.get("revision") == revision:
                    return value
            except (OSError, json.JSONDecodeError):
                pass
        return build_usage_snapshot(rows, "session", session_id=safe_id)

    def _records_path(self, session_id):
        return self.sessions_root / f"{session_id}.records.json"

    def _summary_path(self, session_id):
        return self.sessions_root / f"{session_id}.summary.json"

    @staticmethod
    def _safe_id(value):
        text = str(value or "").strip()
        if not text or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in text):
            return ""
        return text

    @staticmethod
    def _write_json_atomic(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent), suffix=".tmp") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_name = handle.name
        Path(temp_name).replace(path)
