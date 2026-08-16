"""Durable Project Memory — cross-task / cross-session stable knowledge.

Only long-lived, reusable knowledge belongs here: project conventions, key
decisions, build/test commands, environment constraints, dependency facts,
validated workflows, known pitfalls, and explicit stable user preferences.

Never stored here: transient line numbers, one-off test failures, patch text,
per-run blockers/next steps, shell output, current implementation details,
git dirty state, run ids. Those belong to Working State / Raw Artifact /
Evidence Store.

Every record carries provenance (which task / run / evidence / user statement
produced it), a lifecycle status, and a `supersedes` pointer for auditability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import storage

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_RETIRED = "retired"
STATUS_REJECTED = "rejected"
VALID_STATUSES = frozenset(
    {STATUS_ACTIVE, STATUS_SUPERSEDED, STATUS_RETIRED, STATUS_REJECTED}
)

DURABLE_TOPICS = (
    "project-conventions",
    "key-decisions",
    "dependency-facts",
    "user-preferences",
    "build-and-test",
    "environment-constraints",
    "validated-workflows",
    "known-pitfalls",
)

TOPIC_DEFAULTS = {
    "project-conventions": ("Project Conventions", ["convention"]),
    "key-decisions": ("Key Decisions", ["decision"]),
    "dependency-facts": ("Dependency Facts", ["dependency"]),
    "user-preferences": ("User Preferences", ["preference"]),
    "build-and-test": ("Build & Test", ["build", "test"]),
    "environment-constraints": ("Environment Constraints", ["environment"]),
    "validated-workflows": ("Validated Workflows", ["workflow"]),
    "known-pitfalls": ("Known Pitfalls", ["pitfall"]),
}

STATEMENT_LIMIT = 300
RATIONALE_LIMIT = 400
DEFAULT_MAX_RECORDS = 400


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_topic(topic):
    topic = str(topic or "").strip().replace(" ", "-").lower()
    if topic in DURABLE_TOPICS:
        return topic
    # Best-effort mapping of common free-form topic names.
    mapping = {
        "project-convention": "project-conventions",
        "conventions": "project-conventions",
        "decision": "key-decisions",
        "key-decision": "key-decisions",
        "architecture-decision": "key-decisions",
        "dependency": "dependency-facts",
        "dependencies": "dependency-facts",
        "preference": "user-preferences",
        "user-preference": "user-preferences",
        "build": "build-and-test",
        "test": "build-and-test",
        "testing": "build-and-test",
        "environment": "environment-constraints",
        "workflow": "validated-workflows",
        "pitfall": "known-pitfalls",
        "pitfalls": "known-pitfalls",
    }
    return mapping.get(topic, "project-conventions")


@dataclass
class DurableMemoryRecord:
    memory_id: str
    topic: str
    statement: str
    rationale: str = ""
    evidence_refs: list = field(default_factory=list)
    project_scope: str = ""
    tags: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
    use_count: int = 0
    confidence: float = 1.0
    status: str = STATUS_ACTIVE
    source_task_ids: list = field(default_factory=list)
    source_run_ids: list = field(default_factory=list)
    source_evidence_ids: list = field(default_factory=list)
    source_user_statement: str = ""
    supersedes: str = ""
    conflict_with: str = ""

    @classmethod
    def create(cls, topic, statement, **kwargs):
        now_value = kwargs.pop("created_at", None) or _utc_now()
        return cls(
            memory_id="mem_" + uuid4().hex[:12],
            topic=normalize_topic(topic),
            statement=str(statement or "")[:STATEMENT_LIMIT],
            rationale=str(kwargs.pop("rationale", "") or "")[:RATIONALE_LIMIT],
            evidence_refs=[
                str(ref) for ref in (kwargs.pop("evidence_refs", None) or [])
            ],
            project_scope=str(kwargs.pop("project_scope", "") or ""),
            tags=[str(tag)[:60] for tag in (kwargs.pop("tags", None) or [])][:10],
            created_at=now_value,
            updated_at=kwargs.pop("updated_at", None) or now_value,
            last_used_at=str(kwargs.pop("last_used_at", "") or ""),
            use_count=int(kwargs.pop("use_count", 0) or 0),
            confidence=float(kwargs.pop("confidence", 1.0) or 1.0),
            status=str(kwargs.pop("status", STATUS_ACTIVE))
            if str(kwargs.pop("status", STATUS_ACTIVE)) in VALID_STATUSES
            else STATUS_ACTIVE,
            source_task_ids=[
                str(item) for item in (kwargs.pop("source_task_ids", None) or [])
            ],
            source_run_ids=[
                str(item) for item in (kwargs.pop("source_run_ids", None) or [])
            ],
            source_evidence_ids=[
                str(item) for item in (kwargs.pop("source_evidence_ids", None) or [])
            ],
            source_user_statement=str(kwargs.pop("source_user_statement", "") or ""),
            supersedes=str(kwargs.pop("supersedes", "") or ""),
            conflict_with=str(kwargs.pop("conflict_with", "") or ""),
        )

    def to_dict(self):
        return {
            "memory_id": self.memory_id,
            "topic": self.topic,
            "statement": self.statement,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "project_scope": self.project_scope,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "confidence": self.confidence,
            "status": self.status,
            "source_task_ids": list(self.source_task_ids),
            "source_run_ids": list(self.source_run_ids),
            "source_evidence_ids": list(self.source_evidence_ids),
            "source_user_statement": self.source_user_statement,
            "supersedes": self.supersedes,
            "conflict_with": self.conflict_with,
        }

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        status = str(data.get("status") or STATUS_ACTIVE)
        return cls(
            memory_id=str(data.get("memory_id") or "mem_" + uuid4().hex[:12]),
            topic=normalize_topic(str(data.get("topic") or "project-conventions")),
            statement=str(data.get("statement") or "")[:STATEMENT_LIMIT],
            rationale=str(data.get("rationale") or "")[:RATIONALE_LIMIT],
            evidence_refs=[str(ref) for ref in (data.get("evidence_refs") or [])],
            project_scope=str(data.get("project_scope") or ""),
            tags=[str(tag) for tag in (data.get("tags") or [])],
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_used_at=str(data.get("last_used_at") or ""),
            use_count=int(data.get("use_count") or 0),
            confidence=float(data.get("confidence") or 1.0),
            status=status if status in VALID_STATUSES else STATUS_ACTIVE,
            source_task_ids=[str(item) for item in (data.get("source_task_ids") or [])],
            source_run_ids=[str(item) for item in (data.get("source_run_ids") or [])],
            source_evidence_ids=[
                str(item) for item in (data.get("source_evidence_ids") or [])
            ],
            source_user_statement=str(data.get("source_user_statement") or ""),
            supersedes=str(data.get("supersedes") or ""),
            conflict_with=str(data.get("conflict_with") or ""),
        )


class DurableMemoryStore:
    def __init__(self, workspace_root, max_records=DEFAULT_MAX_RECORDS):
        self.workspace_root = Path(workspace_root).resolve()
        self.max_records = int(max_records)
        self.root = self.workspace_root / ".codecub" / "memory" / "v2"
        self.durable_path = self.root / "durable.jsonl"
        self.records = []
        self.corrupt_lines = 0
        self.load()

    def load(self):
        self.records, self.corrupt_lines = storage.read_jsonl(self.durable_path)
        self.records = [
            DurableMemoryRecord.from_dict(record).to_dict() for record in self.records
        ]
        return self

    def save(self):
        storage.rewrite_jsonl(self.durable_path, self.records)
        return self

    # ------------------------------------------------------------------
    # Writes (policy lives in consolidation.py)
    # ------------------------------------------------------------------

    def add(self, record):
        self.records.append(record.to_dict())
        self._enforce_bounds()
        self.save()
        return record

    def _enforce_bounds(self):
        if len(self.records) <= self.max_records:
            return
        self.records.sort(key=lambda record: _ts(record.get("created_at")))
        while len(self.records) > self.max_records:
            removed = self.records.pop(0)
            if removed["status"] == STATUS_SUPERSEDED:
                continue

    def update(self, record):
        for index, existing in enumerate(self.records):
            if existing["memory_id"] == record.memory_id:
                self.records[index] = record.to_dict()
                self.save()
                return record
        return self.add(record)

    def set_status(self, memory_id, status):
        for record in self.records:
            if record["memory_id"] == memory_id and status in VALID_STATUSES:
                record["status"] = status
                record["updated_at"] = _utc_now()
                self.save()
                return True
        return False

    def mark_used(self, memory_id):
        for record in self.records:
            if record["memory_id"] == memory_id and record["status"] == STATUS_ACTIVE:
                record["use_count"] = int(record.get("use_count") or 0) + 1
                record["last_used_at"] = _utc_now()
                self.save()
                return True
        return False

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def active_records(self):
        return [
            record
            for record in self.records
            if record["status"] == STATUS_ACTIVE
        ]

    def all_records(self):
        return list(self.records)

    def size(self):
        return len(self.records)

    def active_size(self):
        return len(self.active_records())


def _ts(value):
    try:
        return datetime.fromisoformat(str(value or "")).timestamp()
    except Exception:
        return 0.0
