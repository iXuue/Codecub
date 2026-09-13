"""Proposal-to-candidate admission without Production mutation.

This adapter deliberately stops before the benchmark-specific ``Candidate``
class.  The benchmark supplies the final object and existing commit applier;
this module validates the proposal, exact target paths and parent tree first.
That keeps worktree/commit/manifest isolation as the only way a candidate can
enter the retained orchestrator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pico.evolver.applier.path_guard import assert_patch_allowed
from pico.evolver.proposal import EvolutionProposal, EvolutionProposalValidator, ProposalValidation


class CandidateAdmissionError(ValueError):
    """A proposal or candidate payload failed the pre-commit admission gate."""


@dataclass(frozen=True)
class CandidateArtifact:
    """Immutable candidate payload awaiting the existing worktree applier."""

    candidate_id: str
    parent_id: str
    parent_sha: str
    proposal: EvolutionProposal
    files: Mapping[str, bytes]
    deletions: tuple[str, ...] = ()
    manifest: Any | None = field(default=None, compare=False)
    committed_sha: str | None = None

    def __post_init__(self) -> None:
        for name in ("candidate_id", "parent_id", "parent_sha"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"CandidateArtifact.{name} must not be empty")
        normalized_files = {str(path).replace("\\", "/"): bytes(value) for path, value in self.files.items()}
        normalized_deletions = tuple(str(path).replace("\\", "/") for path in self.deletions)
        if set(normalized_files) & set(normalized_deletions):
            raise ValueError("candidate files and deletions must be disjoint")
        object.__setattr__(self, "files", normalized_files)
        object.__setattr__(self, "deletions", normalized_deletions)

    @property
    def target_paths(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.files) | set(self.deletions)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "parent_id": self.parent_id,
            "parent_sha": self.parent_sha,
            "proposal": self.proposal.to_dict(),
            "target_paths": list(self.target_paths),
            "deletions": list(self.deletions),
            "committed_sha": self.committed_sha,
            "manifest": getattr(self.manifest, "to_dict", lambda: self.manifest)(),
        }


@dataclass(frozen=True)
class CandidateAdmission:
    accepted: bool
    code: str
    reason: str
    proposal_validation: ProposalValidation
    target_paths: tuple[str, ...] = ()


class CandidateBuilder:
    """Build an isolated candidate payload and optionally hand it to a commit applier."""

    def __init__(
        self,
        validator: EvolutionProposalValidator,
        *,
        commit_applier: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self.validator = validator
        self.commit_applier = commit_applier

    def admit(self, proposal: EvolutionProposal) -> CandidateAdmission:
        result = self.validator.validate(proposal)
        return CandidateAdmission(
            accepted=result.accepted,
            code=result.code,
            reason=result.reason,
            proposal_validation=result,
            target_paths=result.target_paths,
        )

    def build(
        self,
        *,
        candidate_id: str,
        parent_id: str,
        parent_sha: str,
        proposal: EvolutionProposal,
        files: Mapping[str, bytes],
        deletions: tuple[str, ...] | list[str] = (),
    ) -> CandidateArtifact:
        admission = self.admit(proposal)
        if not admission.accepted:
            raise CandidateAdmissionError(f"{admission.code}: {admission.reason}")
        candidate_paths = tuple(sorted(set(str(path).replace("\\", "/") for path in files) | set(deletions)))
        expected_paths = tuple(sorted(admission.target_paths))
        if candidate_paths != expected_paths:
            raise CandidateAdmissionError(
                f"TARGET_PATH_MISMATCH: candidate paths {candidate_paths} do not exactly match "
                f"registered proposal paths {expected_paths}"
            )
        try:
            treeish = None
            if self.validator.repo_root is not None:
                treeish = self.validator.treeish or parent_sha
            assert_patch_allowed(
                candidate_paths,
                repo_root=self.validator.repo_root,
                treeish=treeish,
            )
        except ValueError as exc:
            raise CandidateAdmissionError(f"PATH_NOT_ALLOWED: {exc}") from exc
        return CandidateArtifact(
            candidate_id=candidate_id,
            parent_id=parent_id,
            parent_sha=parent_sha,
            proposal=proposal,
            files=files,
            deletions=tuple(deletions),
        )

    def commit(self, candidate: CandidateArtifact, benchmark_candidate: Any) -> Any:
        """Delegate to the retained commit/worktree applier only after admission."""

        if self.commit_applier is None:
            raise CandidateAdmissionError("COMMIT_APPLIER_MISSING: no isolated commit applier is configured")
        if candidate.committed_sha is not None:
            raise CandidateAdmissionError("CANDIDATE_ALREADY_COMMITTED: candidate identity is immutable")
        return self.commit_applier(candidate.parent_id, benchmark_candidate)


def admit_candidate_proposal(
    proposal: EvolutionProposal,
    *,
    repo_root: str | Path | None = None,
    treeish: str | None = None,
) -> CandidateAdmission:
    validator = EvolutionProposalValidator(repo_root=repo_root, treeish=treeish)
    return CandidateBuilder(validator).admit(proposal)


__all__ = [
    "CandidateAdmission",
    "CandidateAdmissionError",
    "CandidateArtifact",
    "CandidateBuilder",
    "admit_candidate_proposal",
]
