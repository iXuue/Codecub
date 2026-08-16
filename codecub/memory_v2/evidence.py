"""Evidence Store — "where did we previously find what, and can we still trust it".

Evidence records are objective, event-driven (read/symbol/test/verification/resolution),
never free-form "please remember X". They carry source_hash (reusing the Phase 2
`memory.file_freshness` hash system — no second hash infra) and a lifecycle status:

    fresh      -> current hash matches source_hash
    stale      -> file changed since the evidence was recorded (location hint only)
    missing    -> the path no longer exists (location hint only)
    superseded -> a newer record exists for the same (path, symbol, kind)

Storage can grow, but retrieval only ever considers the latest available record
per identity, and the store itself is bounded (retirement of superseded/oldest
records when the cap is exceeded).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..memory import canonicalize_path, file_freshness
from . import secrets as secretlib
from . import storage

STATUS_FRESH = "fresh"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_SUPERSEDED = "superseded"
VALID_STATUSES = frozenset(
    {STATUS_FRESH, STATUS_STALE, STATUS_MISSING, STATUS_SUPERSEDED}
)

_TEST_COMMAND_PATTERN = re.compile(
    r"(?i)(pytest|python\s+-m\s+pytest|uv\s+run|npm\s+test|yarn\s+test|"
    r"make\s+test|make\s+build|tox\s+-e|pdm\s+run|poetry\s+run|"
    r"python\s+test|nosetests|unittest)"
)

EVIDENCE_KINDS = frozenset(
    {
        "source_location",
        "symbol_location",
        "test_command",
        "verification_result",
        "architecture_anchor",
        "dependency_location",
        "config_location",
        "error_resolution",
        "resolution",
    }
)

DEFAULT_MAX_RECORDS = 1000
DEFAULT_SUMMARY_LIMIT = 220
DEFAULT_TAG_LIMIT = 8

SCHEMA_VERSION = 2


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _clip(text, limit):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


@dataclass
class EvidenceRecord:
    evidence_id: str
    path: str
    kind: str
    summary: str
    source_hash: str = ""
    symbol: str = ""
    line_range: str = ""
    project_id: str = ""
    created_at: str = ""
    last_verified_at: str = ""
    last_used_at: str = ""
    task_id: str = ""
    run_id: str = ""
    tool_call_id: str = ""
    event_id: str = ""
    confidence: float = 1.0
    status: str = STATUS_FRESH
    tags: list = field(default_factory=list)

    @classmethod
    def create(
        cls,
        path,
        kind,
        summary,
        source_hash="",
        symbol="",
        line_range="",
        project_id="",
        task_id="",
        run_id="",
        tool_call_id="",
        event_id="",
        confidence=1.0,
        tags=None,
        created_at=None,
        status=STATUS_FRESH,
    ):
        return cls(
            evidence_id="ev_" + uuid4().hex[:12],
            path=str(path),
            kind=str(kind),
            summary=_clip(str(summary or ""), DEFAULT_SUMMARY_LIMIT),
            source_hash=str(source_hash or ""),
            symbol=str(symbol or ""),
            line_range=str(line_range or ""),
            project_id=str(project_id or ""),
            created_at=created_at or _utc_now(),
            last_verified_at=created_at or _utc_now(),
            last_used_at="",
            task_id=str(task_id or ""),
            run_id=str(run_id or ""),
            tool_call_id=str(tool_call_id or ""),
            event_id=str(event_id or ""),
            confidence=float(confidence or 1.0),
            status=str(status) if str(status) in VALID_STATUSES else STATUS_FRESH,
            tags=[str(tag)[:80] for tag in (tags or [])][:DEFAULT_TAG_LIMIT],
        )

    def to_dict(self):
        return {
            "evidence_id": self.evidence_id,
            "path": self.path,
            "kind": self.kind,
            "summary": self.summary,
            "source_hash": self.source_hash,
            "symbol": self.symbol,
            "line_range": self.line_range,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "last_verified_at": self.last_verified_at,
            "last_used_at": self.last_used_at,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "event_id": self.event_id,
            "confidence": self.confidence,
            "status": self.status,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        return cls(
            evidence_id=str(data.get("evidence_id") or "ev_" + uuid4().hex[:12]),
            path=str(data.get("path") or ""),
            kind=str(data.get("kind") or "source_location"),
            summary=str(data.get("summary") or ""),
            source_hash=str(data.get("source_hash") or ""),
            symbol=str(data.get("symbol") or ""),
            line_range=str(data.get("line_range") or ""),
            project_id=str(data.get("project_id") or ""),
            created_at=str(data.get("created_at") or ""),
            last_verified_at=str(data.get("last_verified_at") or ""),
            last_used_at=str(data.get("last_used_at") or ""),
            task_id=str(data.get("task_id") or ""),
            run_id=str(data.get("run_id") or ""),
            tool_call_id=str(data.get("tool_call_id") or ""),
            event_id=str(data.get("event_id") or ""),
            confidence=float(data.get("confidence") or 1.0),
            status=str(data.get("status") or STATUS_FRESH)
            if str(data.get("status") or STATUS_FRESH) in VALID_STATUSES
            else STATUS_FRESH,
            tags=[str(tag) for tag in (data.get("tags") or [])],
        )

    def identity(self):
        """Retrieval identity: only the latest record per identity is considered."""
        return (self.path, self.symbol or "", self.kind)


class EvidenceStore:
    def __init__(self, workspace_root, max_records=DEFAULT_MAX_RECORDS, project_id=""):
        self.workspace_root = Path(workspace_root).resolve()
        self.max_records = int(max_records)
        self.root = self.workspace_root / ".codecub" / "memory" / "v2"
        self.evidence_path = self.root / "evidence.jsonl"
        self.project_id = str(project_id) or self._default_project_id()
        self.records = []
        self.corrupt_lines = 0
        self.load()

    def _default_project_id(self):
        try:
            marker = self.workspace_root / ".git"
            if marker.exists():
                head = marker / "HEAD"
                if head.exists():
                    return "ws_" + hashlib.sha256(
                        str(self.workspace_root).encode("utf-8")
                    ).hexdigest()[:12]
        except OSError:
            pass
        return "ws_" + hashlib.sha256(str(self.workspace_root).encode("utf-8")).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self):
        self.records, self.corrupt_lines = storage.read_jsonl(self.evidence_path)
        self.records = [
            EvidenceRecord.from_dict(record).to_dict() for record in self.records
        ]
        self._prune_superseded()
        return self

    def save(self):
        storage.rewrite_jsonl(self.evidence_path, self.records)
        return self

    def _prune_superseded(self):
        """Keep only the latest record per identity; older ones marked superseded."""
        by_identity = {}
        order = []
        for record in self.records:
            identity = (record["path"], record["symbol"] or "", record["kind"])
            if identity not in by_identity:
                by_identity[identity] = record
                order.append(identity)
            else:
                existing = by_identity[identity]
                if _record_created_at(record) >= _record_created_at(existing):
                    existing["status"] = STATUS_SUPERSEDED
                    by_identity[identity] = record
                else:
                    record["status"] = STATUS_SUPERSEDED
        self.records = list(by_identity.values())

    # ------------------------------------------------------------------
    # Writes (objective event-driven)
    # ------------------------------------------------------------------

    def canonical_path(self, raw_path):
        return canonicalize_path(raw_path, self.workspace_root)

    def record_tool_evidence(
        self,
        name,
        args,
        result_text,
        metadata=None,
        task_id="",
        run_id="",
        tool_call_id="",
        event_id="",
    ):
        """Create evidence records from objective tool events (no LLM input)."""
        metadata = metadata or {}
        created = []
        path = str(args.get("path") or "").strip()
        canonical = self.canonical_path(path) if path else ""
        if name == "read_file":
            if canonical:
                summary = _summarize_read_result(result_text)
                if not secretlib.contains_secret(summary):
                    created.append(
                        self.add_evidence(
                            path=canonical,
                            kind="source_location",
                            summary=summary,
                            symbol="",
                            line_range=_line_range(args),
                            task_id=task_id,
                            run_id=run_id,
                            tool_call_id=tool_call_id,
                            event_id=event_id,
                            tags=["read"],
                        )
                    )
        elif name == "symbol_search":
            query = str(args.get("query") or "").strip()
            if query and canonical not in (".", "") and not secretlib.contains_secret(query):
                created.append(
                    self.add_evidence(
                        path=canonical,
                        kind="symbol_location",
                        summary=f"symbol search '{query}' matched definitions",
                        symbol=query,
                        task_id=task_id,
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        event_id=event_id,
                        tags=["symbol"],
                    )
                )
        elif name == "file_outline":
            if canonical and canonical not in (".", ""):
                created.append(
                    self.add_evidence(
                        path=canonical,
                        kind="architecture_anchor",
                        summary="file outline inspected",
                        task_id=task_id,
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        event_id=event_id,
                        tags=["outline"],
                    )
                )
        elif name == "find_references":
            symbol = str(args.get("symbol") or "").strip()
            if symbol and canonical not in (".", ""):
                created.append(
                    self.add_evidence(
                        path=canonical,
                        kind="architecture_anchor",
                        summary=f"references searched for '{symbol}'",
                        symbol=symbol,
                        task_id=task_id,
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        event_id=event_id,
                        tags=["references"],
                    )
                )
        elif name == "run_shell":
            command = str(args.get("command") or "").strip()
            status = str(metadata.get("tool_status") or "").strip()
            if (
                command
                and status == "ok"
                and _TEST_COMMAND_PATTERN.search(command)
            ):
                created.append(
                    self.add_evidence(
                        path="",
                        kind="verification_result",
                        summary=f"command succeeded: {_clip(command, 120)}",
                        task_id=task_id,
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        event_id=event_id,
                        tags=["verification"],
                    )
                )
        elif name in {"write_file", "patch_file"} and canonical:
            # Edits invalidate nothing structurally: the next read re-verifies.
            # A resolution evidence is added by the runtime at run end after
            # verification (see extraction.py / runtime integration).
            pass
        return [item for item in created if item is not None]

    def add_evidence(self, path, kind, summary, **kwargs):
        """Add one record; same (path, symbol, kind) supersedes the older one.

        Returns the new EvidenceRecord (or None when rejected, e.g. outside
        workspace or secret-bearing). Empty/root paths are allowed only as
        "no file location" (e.g. verified command records).
        """
        raw_path = str(path or "").strip()
        if raw_path in ("", "."):
            canonical = ""
        else:
            canonical = self.canonical_path(raw_path)
            if not canonical or canonical == "." or canonical.startswith(".."):
                return None
        summary = str(summary or "").strip()
        if not summary or secretlib.contains_secret(summary):
            return None
        if kind not in EVIDENCE_KINDS:
            kind = "source_location"
        record = EvidenceRecord.create(
            path=canonical,
            kind=kind,
            summary=summary,
            source_hash=(
                file_freshness(canonical, self.workspace_root) if canonical else ""
            ),
            project_id=self.project_id,
            **kwargs,
        )
        # Supersede older records with the same identity.
        for existing in self.records:
            if existing["status"] != STATUS_SUPERSEDED and (
                existing["path"] == record.path
                and (existing["symbol"] or "") == record.symbol
                and existing["kind"] == record.kind
            ):
                existing["status"] = STATUS_SUPERSEDED
        self.records.append(record.to_dict())
        self._enforce_bounds()
        self.save()
        return record

    def _enforce_bounds(self):
        if len(self.records) <= self.max_records:
            return
        # Retire superseded first, then oldest records (by created_at).
        self.records.sort(key=_record_created_at)
        while len(self.records) > self.max_records:
            removed = self.records.pop(0)
            if removed["status"] == STATUS_SUPERSEDED:
                continue

    # ------------------------------------------------------------------
    # Freshness
    # ------------------------------------------------------------------

    def refresh_freshness(self):
        """Recompute status against the live workspace; returns (stale, missing)."""
        stale = []
        missing = []
        for record in self.records:
            if record["status"] == STATUS_SUPERSEDED:
                continue
            if not record.get("path"):
                # No file location (e.g. verified command): never stale/missing.
                continue
            current = file_freshness(record["path"], self.workspace_root)
            if current is None:
                if record["status"] != STATUS_MISSING:
                    record["status"] = STATUS_MISSING
                    missing.append(record)
            elif current != record["source_hash"]:
                if record["status"] != STATUS_STALE:
                    record["status"] = STATUS_STALE
                    stale.append(record)
            else:
                if record["status"] != STATUS_FRESH:
                    record["status"] = STATUS_FRESH
        if stale or missing:
            self.save()
        return stale, missing

    def revalidate_path(self, path):
        """Mark all records for `path` fresh with the current hash (after a read)."""
        canonical = self.canonical_path(path)
        if not canonical:
            return 0
        current = file_freshness(canonical, self.workspace_root)
        revalidated = 0
        for record in self.records:
            if record["path"] == canonical and record["status"] != STATUS_SUPERSEDED:
                record["last_verified_at"] = _utc_now()
                record["source_hash"] = current or record["source_hash"]
                record["status"] = STATUS_FRESH if current else STATUS_MISSING
                revalidated += 1
        if revalidated:
            self.save()
        return revalidated

    def mark_used(self, evidence_id):
        for record in self.records:
            if record["evidence_id"] == evidence_id:
                record["last_used_at"] = _utc_now()
                self.save()
                return True
        return False

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def latest_records(self):
        """Latest available (non-superseded) records, statuses as-is."""
        return [record for record in self.records if record["status"] != STATUS_SUPERSEDED]

    def size(self):
        return len(self.records)

    def active_size(self):
        return len(self.latest_records())


def _record_created_at(record):
    try:
        return datetime.fromisoformat(str(record.get("created_at") or "")).timestamp()
    except Exception:
        return 0.0


def _line_range(args):
    start = str(args.get("start") or "")
    end = str(args.get("end") or "")
    if start or end:
        return f"{start}-{end}"
    return ""


def _summarize_read_result(result_text, limit=160):
    lines = [
        line.strip() for line in str(result_text or "").splitlines() if line.strip()
    ]
    if not lines:
        return "(empty)"
    if lines[0].startswith("# "):
        lines = lines[1:]
    if not lines:
        return "(empty)"
    summary = " | ".join(lines[:3])
    return _clip(summary, limit)
