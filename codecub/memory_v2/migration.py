"""Migration from legacy Memory v1 artifacts into Memory 2.0 stores.

Legacy inputs (never deleted, only read):
- session memory dict (`session["memory"]`): file_summaries / episodic / working
- `.codecub/memory/MEMORY.md` + `topics/*.md` (v1 durable markdown)

Mapping (spec §49):
- old file_summaries          -> Evidence candidate (path + summary + source_hash)
- old durable topic notes     -> Durable Memory active records
- old episodic process notes  -> NOT upgraded (session breadcrumbs only)
- old task_summary/recent_files -> not migrated (Phase 2 WorkingState takes over)

Idempotency: index.json records schema_version + migrated marker; repeated
startups skip re-migration. Corrupt legacy data fails safely (empty + flag).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..memory import DurableMemoryStore as LegacyDurableMemoryStore
from ..memory import canonicalize_path, file_freshness
from . import storage
from .durable import (
    DurableMemoryRecord,
    DurableMemoryStore,
    normalize_topic,
)
from .evidence import EvidenceRecord, EvidenceStore, STATUS_FRESH, STATUS_STALE

SCHEMA_VERSION = 2


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MigrationReport:
    ran: bool = False
    idempotent_skip: bool = False
    migrated_evidence: int = 0
    migrated_durable: int = 0
    skipped_episodic: int = 0
    skipped_working: bool = False
    corrupt_legacy_session: bool = False
    corrupt_legacy_durable: bool = False
    notes: list = field(default_factory=list)

    def to_dict(self):
        return {
            "ran": self.ran,
            "idempotent_skip": self.idempotent_skip,
            "migrated_evidence": self.migrated_evidence,
            "migrated_durable": self.migrated_durable,
            "skipped_episodic": self.skipped_episodic,
            "skipped_working": self.skipped_working,
            "corrupt_legacy_session": self.corrupt_legacy_session,
            "corrupt_legacy_durable": self.corrupt_legacy_durable,
            "notes": list(self.notes),
        }


class MemoryMigration:
    def __init__(self, workspace_root):
        self.workspace_root = Path(workspace_root).resolve()
        self.v2_root = self.workspace_root / ".codecub" / "memory" / "v2"
        self.index_path = self.v2_root / "index.json"

    def already_migrated(self):
        index = storage.read_json_safe(self.index_path, {}) or {}
        return int(index.get("schema_version") or 0) >= SCHEMA_VERSION and bool(
            index.get("migrated")
        )

    def mark_migrated(self, report):
        index = storage.read_json_safe(self.index_path, {}) or {}
        index.update(
            {
                "schema_version": SCHEMA_VERSION,
                "migrated": True,
                "migrated_at": _utc_now(),
                "legacy_evidence_count": report.migrated_evidence,
                "legacy_durable_count": report.migrated_durable,
            }
        )
        storage.write_json_atomic(self.index_path, index)

    def migrate(self, legacy_session_memory=None):
        """Run migration once; returns MigrationReport. Idempotent + safe."""
        if self.already_migrated():
            return MigrationReport(ran=False, idempotent_skip=True)
        report = MigrationReport(ran=True)
        evidence_store = EvidenceStore(self.workspace_root)
        durable_store = DurableMemoryStore(self.workspace_root)

        # 1) Legacy session memory -> Evidence + (skip) breadcrumbs.
        session_memory = legacy_session_memory or {}
        if not isinstance(session_memory, dict):
            report.corrupt_legacy_session = True
            session_memory = {}
        file_summaries = session_memory.get("file_summaries") or {}
        if not isinstance(file_summaries, dict):
            report.corrupt_legacy_session = True
            file_summaries = {}
        for raw_path, summary in file_summaries.items():
            canonical = canonicalize_path(raw_path, self.workspace_root)
            if not canonical:
                continue
            if isinstance(summary, dict):
                text = str(summary.get("summary", "") or "").strip()
                recorded_hash = str(summary.get("freshness") or "").strip() or ""
            else:
                text = str(summary or "").strip()
                recorded_hash = ""
            if not canonical or not text:
                continue
            current = file_freshness(canonical, self.workspace_root)
            status = STATUS_FRESH if (current and current == recorded_hash) else STATUS_STALE
            record = EvidenceRecord.create(
                path=canonical,
                kind="source_location",
                summary=text[:220],
                source_hash=recorded_hash,
                status=status,
                created_at=str(summary.get("created_at")) if isinstance(summary, dict) else "",
                tags=["migrated"],
            )
            evidence_store.records.append(record.to_dict())
            report.migrated_evidence += 1
        if report.migrated_evidence:
            evidence_store.save()

        episodic = session_memory.get("episodic_notes") or []
        report.skipped_episodic = len(episodic) if isinstance(episodic, list) else 0
        report.skipped_working = bool(
            (session_memory.get("working") or {}).get("task_summary")
            or (session_memory.get("working") or {}).get("recent_files")
        )

        # 2) Legacy durable markdown -> Durable Memory active records.
        legacy_root = self.workspace_root / ".codecub" / "memory"
        try:
            legacy_store = LegacyDurableMemoryStore(legacy_root)
            topics = legacy_store.load_index()
            for topic_meta in topics:
                slug = str(topic_meta.get("topic", "")).strip()
                if not slug:
                    continue
                try:
                    notes = legacy_store.load_topic_notes(slug)
                except Exception:
                    report.corrupt_legacy_durable = True
                    notes = []
                for note in notes:
                    statement = str(note.get("text", "") or "").strip()
                    if not statement:
                        continue
                    durable_store.add(
                        DurableMemoryRecord.create(
                            topic=normalize_topic(slug),
                            statement=statement[:300],
                            rationale="migrated from legacy durable memory",
                            source_user_statement="(migrated from legacy durable memory)",
                            created_at=str(note.get("created_at") or ""),
                        )
                    )
                    report.migrated_durable += 1
        except Exception:
            report.corrupt_legacy_durable = True

        self.mark_migrated(report)
        return report
