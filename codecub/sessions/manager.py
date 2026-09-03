"""Persistent session identity and shape management.

Resume validation deliberately remains in the Runtime adapter for now because
it depends on Memory v1/v2 freshness.  This boundary owns storage and the
stable session data contract without importing surfaces, Spine, or tools.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4


class SessionStore:
    """JSON compatibility store retained under its existing public name."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id):
        return self.root / f"{session_id}.json"

    def save(self, session):
        path = self.path(session["id"])
        path.write_text(json.dumps(session, indent=2), encoding="utf-8")
        return path

    def load(self, session_id):
        return json.loads(self.path(session_id).read_text(encoding="utf-8"))

    def latest(self):
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        return files[-1].stem if files else None


class SessionManager:
    """Owns session creation, persistence, and schema normalization."""

    def __init__(self, store: SessionStore, workspace_root, default_memory):
        self.store = store
        self.workspace_root = str(workspace_root)
        self.default_memory = default_memory

    def create(self, session=None, now=""):
        value = dict(session or {})
        value.setdefault("id", datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
        value.setdefault("created_at", now)
        value.setdefault("workspace_root", self.workspace_root)
        self.ensure_shape(value)
        return value

    def load(self, session_id):
        value = self.store.load(session_id)
        self.ensure_shape(value)
        return value

    def save(self, session):
        self.ensure_shape(session)
        return self.store.save(session)

    def ensure_shape(self, session):
        session.setdefault("history", [])
        session.setdefault("memory", self.default_memory())
        checkpoints = session.setdefault("checkpoints", {})
        if not isinstance(checkpoints, dict):
            checkpoints = {}
            session["checkpoints"] = checkpoints
        checkpoints.setdefault("current_id", "")
        checkpoints.setdefault("items", {})
        for key in ("runtime_identity", "resume_state"):
            if not isinstance(session.setdefault(key, {}), dict):
                session[key] = {}
        return session

    @staticmethod
    def checkpoint_state(session):
        return session["checkpoints"]

    @classmethod
    def current_checkpoint(cls, session):
        state = cls.checkpoint_state(session)
        checkpoint_id = str(state.get("current_id", "")).strip()
        return state.get("items", {}).get(checkpoint_id) if checkpoint_id else None

    def evaluate_resume(
        self, session, *, invalidated, file_freshness, runtime_identity, schema_version, statuses
    ):
        """Compute and persist resume metadata without owning Runtime state."""
        previous = dict(session.get("resume_state", {}) or {})
        checkpoint = self.current_checkpoint(session)
        status = statuses["none"]
        stale_paths = list(invalidated)
        mismatches = []
        if checkpoint:
            if checkpoint.get("schema_version") != schema_version:
                status = statuses["schema_mismatch"]
            else:
                for item in checkpoint.get("key_files", []):
                    path = str(item.get("path", "")).strip()
                    if path and item.get("freshness") != file_freshness(path) and path not in stale_paths:
                        stale_paths.append(path)
                saved = dict(checkpoint.get("runtime_identity", {}) or session.get("runtime_identity", {}) or {})
                current = runtime_identity()
                for key in (
                    "cwd", "model", "model_client", "approval_policy", "read_only", "max_steps",
                    "runtime_mode", "emergency_cap", "max_new_tokens", "feature_flags",
                    "shell_env_allowlist", "workspace_fingerprint", "tool_signature",
                ):
                    if key in saved and saved.get(key) != current.get(key):
                        mismatches.append(key)
                mismatches.sort()
                status = statuses["partial_stale"] if stale_paths else (
                    statuses["workspace_mismatch"] if mismatches else statuses["full_valid"]
                )
        result = {
            "status": status,
            "stale_paths": stale_paths,
            "runtime_identity_mismatch_fields": mismatches,
            "stale_summary_invalidations": max(
                len(invalidated),
                int(previous.get("stale_summary_invalidations", 0))
                if status == statuses["partial_stale"] else 0,
            ),
        }
        session["resume_state"] = result
        session["runtime_identity"] = runtime_identity()
        return result
