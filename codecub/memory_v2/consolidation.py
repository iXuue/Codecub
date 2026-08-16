"""Memory Consolidation — duplicate / merge / supersede / conflict / reject.

Every new durable candidate is checked against existing active records before
persistence (spec §28-§31). Physical deletion never happens: superseded and
retired records stay for audit, they just stop being retrieval candidates.

Decisions:
    NEW        no comparable record
    DUPLICATE  semantically identical -> touch updated_at / last_used_at / refs
    MERGE      overlapping statements -> keep the richer statement, supersede the rest
    SUPERSEDE  same subject, newer candidate has verification evidence
    CONFLICT   same subject, contradictory statements, no decisive evidence
    REJECT     defensive filter (secrets / transient / unverified)
"""

from __future__ import annotations

import re

from .durable import (
    DurableMemoryRecord,
    STATUS_SUPERSEDED,
    TOPIC_DEFAULTS,
    normalize_topic,
)
from .extraction import MemoryCandidate, reject_candidate

ACTION_NEW = "NEW"
ACTION_DUPLICATE = "DUPLICATE"
ACTION_MERGE = "MERGE"
ACTION_SUPERSEDE = "SUPERSEDE"
ACTION_CONFLICT = "CONFLICT"
ACTION_REJECT = "REJECT"

SUBJECT_PATTERNS = (
    re.compile(r"^(.+?)\s+is\s*:?\s+.+$", re.I),
    re.compile(r"^(.+?)\s+are\s*:?\s+.+$", re.I),
    re.compile(r"^(.+?)\s+uses?\s*:?\s+.+$", re.I),
    re.compile(r"^(.+?)\s+should\s*:?\s+.+$", re.I),
    re.compile(r"^(.+?)是.+$"),
    re.compile(r"^(.+?)使用.+$"),
)


def _tokens(text):
    return set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", str(text).lower()))


def _subject_key(statement):
    text = str(statement or "").strip()
    for pattern in SUBJECT_PATTERNS:
        match = pattern.match(text)
        if match:
            subject = " ".join(sorted(_tokens(match.group(1))))
            if subject:
                return subject
    return None


def _token_overlap(a, b):
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


_NUMBER_TOKEN_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")


def _contradictory(a, b):
    """True when two statements say the same thing about different values.

    e.g. "Project uses Python 3.11" vs "Project uses Python 3.13":
    non-numeric tokens are identical but numeric values differ -> contradictory,
    so MERGE must not silently overwrite one with the other.
    """
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    numbers_a = {token for token in tokens_a if _NUMBER_TOKEN_PATTERN.match(token)}
    numbers_b = {token for token in tokens_b if _NUMBER_TOKEN_PATTERN.match(token)}
    if numbers_a == numbers_b:
        return False
    return tokens_a - numbers_a == tokens_b - numbers_b


class ConsolidationOutcome:
    def __init__(self, action, record=None, existing=None, reason=""):
        self.action = action
        self.record = record  # the record that is now active (new or updated existing)
        self.existing = existing  # affected existing record (superseded/conflict)
        self.reason = reason

    def to_dict(self):
        return {
            "action": self.action,
            "record_id": self.record.memory_id if self.record else None,
            "existing_id": self.existing.memory_id if self.existing else None,
            "reason": self.reason,
        }


class MemoryConsolidator:
    def __init__(self, store):
        self.store = store

    def apply(self, candidate, source_task_id="", source_run_id=""):
        """Consolidate one candidate into the durable store.

        Returns ConsolidationOutcome. Trace events are described in the outcome
        (runtime/facade emits them with the correct payload).
        """
        candidate = candidate if isinstance(candidate, MemoryCandidate) else MemoryCandidate(
            candidate_type=str(getattr(candidate, "candidate_type", "convention")),
            statement=str(getattr(candidate, "statement", "")),
        )
        reason = reject_candidate(candidate)
        if reason:
            return ConsolidationOutcome(ACTION_REJECT, reason=reason)

        topic = normalize_topic(candidate.topic)
        statement = str(candidate.statement or "").strip()
        active = [
            record
            for record in self.store.active_records()
            if record["topic"] == topic
        ]
        subject = _subject_key(statement)
        candidate_has_verification = any(
            str(ref.get("kind", "")).startswith("verification")
            for ref in candidate.source_refs
            if isinstance(ref, dict)
        )

        # 1) Exact duplicate: touch timestamps + provenance, no new record.
        for record in active:
            if str(record.get("statement", "")).strip().lower() == statement.lower():
                updated = DurableMemoryRecord.from_dict(record)
                updated.updated_at = _nowish()
                updated.source_task_ids = _append_unique(
                    updated.source_task_ids, source_task_id
                )
                updated.source_run_ids = _append_unique(
                    updated.source_run_ids, source_run_id
                )
                for ref in candidate.source_refs:
                    if isinstance(ref, dict) and ref.get("kind") == "user_final_answer_line":
                        updated.source_user_statement = (
                            updated.source_user_statement or candidate.source_user_statement
                        )
                self.store.update(updated)
                return ConsolidationOutcome(ACTION_DUPLICATE, record=updated, existing=updated)

        # 2) Locate the closest existing record and contradiction state.
        best_overlap = 0.0
        best_record = None
        for record in active:
            overlap = _token_overlap(statement, record.get("statement", ""))
            if overlap > best_overlap:
                best_overlap = overlap
                best_record = record
        contradictory = bool(
            best_record is not None
            and best_overlap >= 0.6
            and _contradictory(best_record.get("statement", ""), statement)
        )
        same_subject = [
            record
            for record in active
            if subject and _subject_key(record.get("statement", "")) == subject
        ]
        conflict_candidate = same_subject[0] if same_subject else (
            best_record if contradictory else None
        )

        if conflict_candidate is not None:
            existing = conflict_candidate
            if candidate_has_verification:
                # Verified newer fact supersedes the old record (spec §30).
                self.store.set_status(existing["memory_id"], STATUS_SUPERSEDED)
                record = self._new_record(candidate, topic, source_task_id, source_run_id)
                record.supersedes = existing["memory_id"]
                self.store.add(record)
                return ConsolidationOutcome(
                    ACTION_SUPERSEDE, record=record, existing=existing
                )
            if contradictory:
                # CONFLICT: no decisive evidence; do not auto-overwrite (spec §31).
                record = self._new_record(candidate, topic, source_task_id, source_run_id)
                record.conflict_with = existing["memory_id"]
                self.store.add(record)
                existing_record = DurableMemoryRecord.from_dict(existing)
                existing_record.conflict_with = (
                    existing_record.conflict_with or record.memory_id
                )
                self.store.update(existing_record)
                return ConsolidationOutcome(
                    ACTION_CONFLICT, record=record, existing=existing_record
                )
            # Same subject, compatible wording: fall through to overlap merge.

        # 3) Overlap merge: richer statement replaces the older one in place
        #    (only when not contradictory).
        if (
            best_overlap >= 0.6
            and best_record is not None
            and not contradictory
        ):
            existing_tokens = _tokens(best_record.get("statement", ""))
            new_tokens = _tokens(statement)
            if new_tokens >= existing_tokens:
                updated = DurableMemoryRecord.from_dict(best_record)
                updated.statement = statement
                updated.updated_at = _nowish()
                updated.source_task_ids = _append_unique(
                    updated.source_task_ids, source_task_id
                )
                updated.source_run_ids = _append_unique(
                    updated.source_run_ids, source_run_id
                )
                self.store.update(updated)
                return ConsolidationOutcome(ACTION_MERGE, record=updated, existing=updated)
            updated = DurableMemoryRecord.from_dict(best_record)
            updated.updated_at = _nowish()
            self.store.update(updated)
            return ConsolidationOutcome(ACTION_DUPLICATE, record=updated, existing=updated)

        # 4) NEW
        record = self._new_record(candidate, topic, source_task_id, source_run_id)
        self.store.add(record)
        return ConsolidationOutcome(ACTION_NEW, record=record)

    def _new_record(self, candidate, topic, source_task_id, source_run_id):
        record = DurableMemoryRecord.create(
            topic=topic,
            statement=candidate.statement,
            rationale=str(candidate.reason_to_remember or "")[:400],
            evidence_refs=[str(ref) for ref in candidate.source_refs],
            tags=list(TOPIC_DEFAULTS.get(topic, ("", []))[1]),
            confidence=candidate.confidence,
            project_scope=str(candidate.scope or "project"),
            source_task_ids=[source_task_id] if source_task_id else [],
            source_run_ids=[source_run_id] if source_run_id else [],
            source_evidence_ids=list(candidate.source_evidence_ids or []),
            source_user_statement=str(candidate.source_user_statement or ""),
        )
        return record


def _nowish():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _append_unique(items, value):
    value = str(value or "")
    if not value or value in items:
        return list(items)
    return [*items, value]
