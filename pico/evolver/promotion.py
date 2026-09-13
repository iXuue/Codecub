"""Candidate-level promotion, human finalize and lineage ledger.

The retained orchestrator may decide that a candidate is better, but this
module makes the durable lifecycle explicit: a gate can produce
``PROMOTABLE`` evidence only; an explicit human action is the sole transition
to ``ACTIVE``.  Holdout and regression values are accepted as gate evidence
but are never included in proposal or failure-miner contexts.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class PromotionStatus(StrEnum):
    REJECTED = "REJECTED"
    PROMOTABLE = "PROMOTABLE"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class GateEvidence:
    development_passed: bool
    holdout_passed: bool
    regression_passed: bool
    measurement_valid: bool = True
    holdout_leak: bool = False

    @property
    def passed(self) -> bool:
        return all(
            (
                self.development_passed,
                self.holdout_passed,
                self.regression_passed,
                self.measurement_valid,
                not self.holdout_leak,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "development_passed": self.development_passed,
            "holdout_passed": self.holdout_passed,
            "regression_passed": self.regression_passed,
            "measurement_valid": self.measurement_valid,
            "holdout_leak": self.holdout_leak,
        }


@dataclass(frozen=True)
class PromotionDecision:
    candidate_id: str
    status: PromotionStatus
    reason: str
    evidence: GateEvidence
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def promotable(self) -> bool:
        return self.status is PromotionStatus.PROMOTABLE

    @property
    def active(self) -> bool:
        return self.status is PromotionStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status.value,
            "reason": self.reason,
            "evidence": self.evidence.to_dict(),
            "created_at": self.created_at,
        }


class PromotionGate:
    """Pure candidate-level gate; it never activates a candidate."""

    def evaluate(
        self,
        candidate_id: str,
        evidence: GateEvidence,
        *,
        reason: str | None = None,
    ) -> PromotionDecision:
        if not str(candidate_id).strip():
            raise ValueError("candidate_id must not be empty")
        if evidence.holdout_leak:
            final_reason = "holdout material leaked into the optimization/evaluation context"
        elif not evidence.measurement_valid:
            final_reason = "measurement is invalid or incomplete"
        elif not evidence.development_passed:
            final_reason = "development gate failed"
        elif not evidence.holdout_passed:
            final_reason = "holdout gate failed"
        elif not evidence.regression_passed:
            final_reason = "regression gate failed"
        else:
            final_reason = reason or "development, holdout and regression gates passed; human finalize required"
        return PromotionDecision(
            candidate_id=str(candidate_id).strip(),
            status=PromotionStatus.PROMOTABLE if evidence.passed else PromotionStatus.REJECTED,
            reason=final_reason,
            evidence=evidence,
        )


@dataclass(frozen=True)
class LineageRecord:
    candidate_id: str
    parent_id: str
    parent_sha: str
    candidate_sha: str
    proposal: dict[str, Any]
    manifest_digest: str
    decision: PromotionDecision
    human_actor: str | None = None
    finalized_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "parent_id": self.parent_id,
            "parent_sha": self.parent_sha,
            "candidate_sha": self.candidate_sha,
            "proposal": dict(self.proposal),
            "manifest_digest": self.manifest_digest,
            "decision": self.decision.to_dict(),
            "human_actor": self.human_actor,
            "finalized_at": self.finalized_at,
        }


class PromotionLedger:
    """Crash-safe JSON ledger for candidate promotion and complete lineage."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records: dict[str, LineageRecord] = {}
        self._load()

    def record_gate(
        self,
        *,
        parent_id: str,
        parent_sha: str,
        candidate_sha: str,
        proposal: Any,
        manifest_digest: str,
        decision: PromotionDecision,
    ) -> LineageRecord:
        if decision.status is PromotionStatus.ACTIVE:
            raise ValueError("Promotion Gate cannot directly create ACTIVE records")
        candidate_id = decision.candidate_id
        if candidate_id in self._records:
            raise ValueError(f"candidate {candidate_id!r} already has a lineage record")
        proposal_dict = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal)
        record = LineageRecord(
            candidate_id=candidate_id,
            parent_id=str(parent_id),
            parent_sha=str(parent_sha),
            candidate_sha=str(candidate_sha),
            proposal=proposal_dict,
            manifest_digest=str(manifest_digest),
            decision=decision,
        )
        self._records[candidate_id] = record
        self._save()
        return record

    def finalize(self, candidate_id: str, *, actor: str, approved: bool) -> LineageRecord:
        candidate_id = str(candidate_id).strip()
        actor = str(actor).strip()
        if not actor:
            raise ValueError("human finalize requires a non-empty actor")
        try:
            current = self._records[candidate_id]
        except KeyError as exc:
            raise KeyError(f"unknown candidate lineage: {candidate_id!r}") from exc
        if current.decision.status is not PromotionStatus.PROMOTABLE:
            raise ValueError(
                f"candidate {candidate_id!r} is {current.decision.status.value}; only PROMOTABLE candidates "
                "may be finalized"
            )
        now = datetime.now(timezone.utc).isoformat()
        if approved:
            decision = PromotionDecision(
                candidate_id=candidate_id,
                status=PromotionStatus.ACTIVE,
                reason="explicit human finalize approved activation",
                evidence=current.decision.evidence,
                created_at=current.decision.created_at,
            )
            updated = LineageRecord(
                **{**current.__dict__, "decision": decision, "human_actor": actor, "finalized_at": now}
            )
        else:
            decision = PromotionDecision(
                candidate_id=candidate_id,
                status=PromotionStatus.REJECTED,
                reason="explicit human finalize rejected activation",
                evidence=current.decision.evidence,
                created_at=current.decision.created_at,
            )
            updated = LineageRecord(
                **{**current.__dict__, "decision": decision, "human_actor": actor, "finalized_at": now}
            )
        self._records[candidate_id] = updated
        self._save()
        return updated

    def get(self, candidate_id: str) -> LineageRecord | None:
        return self._records.get(str(candidate_id).strip())

    def all(self) -> tuple[LineageRecord, ...]:
        return tuple(self._records.values())

    def _load(self) -> None:
        if not self.path.is_file():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("invalid PromotionLedger schema")
        for candidate_id, value in dict(raw.get("records") or {}).items():
            decision_raw = value["decision"]
            evidence_raw = decision_raw["evidence"]
            evidence = GateEvidence(**evidence_raw)
            decision = PromotionDecision(
                candidate_id=decision_raw["candidate_id"],
                status=PromotionStatus(decision_raw["status"]),
                reason=decision_raw["reason"],
                evidence=evidence,
                created_at=decision_raw["created_at"],
            )
            self._records[candidate_id] = LineageRecord(
                candidate_id=value["candidate_id"],
                parent_id=value["parent_id"],
                parent_sha=value["parent_sha"],
                candidate_sha=value["candidate_sha"],
                proposal=dict(value["proposal"]),
                manifest_digest=value["manifest_digest"],
                decision=decision,
                human_actor=value.get("human_actor"),
                finalized_at=value.get("finalized_at"),
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "records": {candidate_id: record.to_dict() for candidate_id, record in self._records.items()},
        }
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            Path(temp_name).replace(self.path)
        finally:
            temp = Path(temp_name)
            if temp.exists():
                temp.unlink()


__all__ = [
    "GateEvidence",
    "LineageRecord",
    "PromotionDecision",
    "PromotionGate",
    "PromotionLedger",
    "PromotionStatus",
]
