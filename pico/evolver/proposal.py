"""受控 EvolutionProposal 契约与 fail-closed target 适配器。

The proposal is the boundary between diagnosis and mutation.  It describes
what may be changed; it is not a patch and never authorizes a write by itself.
Only Prompt, Tool/Policy and Skill are enabled in this Evolver V2 scope.
``WORKFLOW`` remains an explicit extension point, but is rejected until a
separate, reviewed workflow runtime contract exists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pico.evolver.applier.path_guard import check_patch_paths
from pico.evolver.evaluation.registry import EvaluationRegistry, EvaluationSplit, HoldoutLeakError

if False:  # pragma: no cover - type-checking-only import without runtime cycle
    from pico.evolver.analysis.failure_miner import FailureMiningReport


class EvolutionTargetLayer(StrEnum):
    PROMPT = "PROMPT"
    TOOL_POLICY = "TOOL_POLICY"
    SKILL = "SKILL"
    WORKFLOW = "WORKFLOW"

    @classmethod
    def parse(cls, value: "EvolutionTargetLayer | str") -> "EvolutionTargetLayer":
        if isinstance(value, cls):
            return value
        normalized = re.sub(r"[^A-Z0-9]+", "_", str(value).strip().upper()).strip("_")
        aliases = {
            "TOOL": cls.TOOL_POLICY,
            "POLICY": cls.TOOL_POLICY,
            "TOOL_POLICY": cls.TOOL_POLICY,
            "PROMPT": cls.PROMPT,
            "SKILL": cls.SKILL,
            "WORKFLOW": cls.WORKFLOW,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown EvolutionProposal target_layer: {value!r}") from exc


class ProposalAction(StrEnum):
    MODIFY_EXISTING = "MODIFY_EXISTING"
    CREATE_NEW = "CREATE_NEW"

    @classmethod
    def parse(cls, value: "ProposalAction | str") -> "ProposalAction":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"unknown EvolutionProposal action: {value!r}") from exc


@dataclass(frozen=True)
class EvolutionProposal:
    """A structured, reviewable proposal emitted after failure mining."""

    target_layer: EvolutionTargetLayer | str
    action: ProposalAction | str
    target_id: str
    root_cause: str
    evidence: tuple[Any, ...] | Iterable[Any]
    reason: str
    repeated_pattern_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_layer", EvolutionTargetLayer.parse(self.target_layer))
        object.__setattr__(self, "action", ProposalAction.parse(self.action))
        for name in ("target_id", "root_cause", "reason"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"EvolutionProposal.{name} must not be empty")
            object.__setattr__(self, name, value)
        evidence = self.evidence if isinstance(self.evidence, tuple) else tuple(self.evidence)
        if not evidence:
            raise ValueError("EvolutionProposal.evidence must not be empty")
        object.__setattr__(self, "evidence", evidence)
        if self.repeated_pattern_count < 0:
            raise ValueError("repeated_pattern_count must be non-negative")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_layer": self.target_layer.value,
            "action": self.action.value,
            "target_id": self.target_id,
            "root_cause": self.root_cause,
            "evidence": [_jsonable(item) for item in self.evidence],
            "reason": self.reason,
            "repeated_pattern_count": self.repeated_pattern_count,
            "metadata": dict(self.metadata),
        }


def _jsonable(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class TargetDescriptor:
    layer: EvolutionTargetLayer | str
    target_id: str
    paths: tuple[str, ...]
    label: str
    allow_create: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "layer", EvolutionTargetLayer.parse(self.layer))
        target_id = str(self.target_id).strip()
        if not target_id:
            raise ValueError("TargetDescriptor.target_id must not be empty")
        object.__setattr__(self, "target_id", target_id)
        paths = tuple(str(path).replace("\\", "/") for path in self.paths)
        if not paths:
            raise ValueError("TargetDescriptor.paths must not be empty")
        object.__setattr__(self, "paths", paths)
        if not str(self.label).strip():
            raise ValueError("TargetDescriptor.label must not be empty")


class ProposalTargetRegistry:
    """Explicit extension point for future target adapters.

    Registration is data-only.  It does not make an otherwise unsupported
    layer executable; the validator still enforces the current V2 scope.
    """

    def __init__(self, descriptors: Iterable[TargetDescriptor] = ()) -> None:
        self._targets: dict[tuple[EvolutionTargetLayer, str], TargetDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: TargetDescriptor) -> None:
        key = (descriptor.layer, descriptor.target_id)
        if key in self._targets:
            raise ValueError(f"duplicate EvolutionProposal target: {descriptor.layer.value}/{descriptor.target_id}")
        self._targets[key] = descriptor

    def get(self, layer: EvolutionTargetLayer | str, target_id: str) -> TargetDescriptor | None:
        return self._targets.get((EvolutionTargetLayer.parse(layer), str(target_id).strip()))

    def require(self, layer: EvolutionTargetLayer | str, target_id: str) -> TargetDescriptor:
        descriptor = self.get(layer, target_id)
        if descriptor is None:
            raise KeyError(f"unknown EvolutionProposal target: {EvolutionTargetLayer.parse(layer).value}/{target_id}")
        return descriptor

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "target_layer": descriptor.layer.value,
                "target_id": descriptor.target_id,
                "paths": list(descriptor.paths),
                "label": descriptor.label,
                "allow_create": descriptor.allow_create,
            }
            for descriptor in self._targets.values()
        ]


def default_target_registry() -> ProposalTargetRegistry:
    """Return the currently supported AppWorld target surface.

    The paths are existing agent-surface files or existing skill content.  No
    runtime or memory implementation file is registered as an evolvable target.
    """

    return ProposalTargetRegistry(
        [
            TargetDescriptor(
                EvolutionTargetLayer.PROMPT,
                "appworld.prompt",
                ("benchmarks/appworld/agent_cli.py",),
                "prompt",
            ),
            TargetDescriptor(
                EvolutionTargetLayer.TOOL_POLICY,
                "appworld.tool_policy",
                ("benchmarks/appworld/tool.py",),
                "policy",
            ),
            TargetDescriptor(
                EvolutionTargetLayer.SKILL,
                "builtin.weather",
                ("pico/memory_engine/skills/weather/SKILL.md",),
                "skill",
            ),
        ]
    )


@dataclass(frozen=True)
class ProposalValidation:
    accepted: bool
    code: str
    reason: str
    target_paths: tuple[str, ...] = ()
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "code": self.code,
            "reason": self.reason,
            "target_paths": list(self.target_paths),
            "label": self.label,
        }


_SAFE_SKILL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EvolutionProposalValidator:
    """Validate Proposal semantics, target identity and path trust boundary."""

    def __init__(
        self,
        registry: ProposalTargetRegistry | None = None,
        *,
        repo_root: str | Path | None = None,
        treeish: str | None = None,
        new_skill_root: str = "pico/memory_engine/skills",
        min_repeated_skill_pattern: int = 3,
    ) -> None:
        self.registry = registry or default_target_registry()
        self.repo_root = Path(repo_root) if repo_root is not None else None
        self.treeish = treeish
        self.new_skill_root = new_skill_root.strip("/").replace("\\", "/")
        self.min_repeated_skill_pattern = min_repeated_skill_pattern
        if min_repeated_skill_pattern < 1:
            raise ValueError("min_repeated_skill_pattern must be >= 1")

    def validate(self, proposal: EvolutionProposal) -> ProposalValidation:
        layer = proposal.target_layer
        if layer is EvolutionTargetLayer.WORKFLOW:
            return ProposalValidation(
                False,
                "WORKFLOW_NOT_IMPLEMENTED",
                "WORKFLOW target is not implemented in Evolver V2; no Agent Workflow Runtime is available. "
                "Register a future reviewed workflow adapter before enabling this layer.",
            )
        if layer not in {
            EvolutionTargetLayer.PROMPT,
            EvolutionTargetLayer.TOOL_POLICY,
            EvolutionTargetLayer.SKILL,
        }:
            return ProposalValidation(False, "UNKNOWN_TARGET_LAYER", f"target layer {layer.value!r} is unsupported")

        descriptor = self.registry.get(layer, proposal.target_id)
        if proposal.action is ProposalAction.CREATE_NEW:
            if layer is not EvolutionTargetLayer.SKILL:
                return ProposalValidation(
                    False,
                    "CREATE_NEW_UNSUPPORTED",
                    "CREATE_NEW is restricted to Skill and requires a stable repeated pattern; "
                    "Prompt and Tool/Policy must modify an existing target",
                )
            if descriptor is not None:
                return ProposalValidation(
                    False,
                    "TARGET_ALREADY_EXISTS",
                    f"Skill target {proposal.target_id!r} already exists; use MODIFY_EXISTING",
                )
            if not _SAFE_SKILL_ID.fullmatch(proposal.target_id):
                return ProposalValidation(
                    False,
                    "UNKNOWN_TARGET",
                    "new Skill target_id must be a safe simple identifier",
                )
            if proposal.repeated_pattern_count < self.min_repeated_skill_pattern:
                return ProposalValidation(
                    False,
                    "SKILL_PATTERN_NOT_STABLE",
                    f"new Skill requires repeated_pattern_count >= {self.min_repeated_skill_pattern}",
                )
            paths = (f"{self.new_skill_root}/{proposal.target_id}/SKILL.md",)
            return self._path_result(proposal, paths, "skill")

        if descriptor is None:
            return ProposalValidation(
                False,
                "UNKNOWN_TARGET",
                f"no registered target {layer.value}/{proposal.target_id}; arbitrary target paths are rejected",
            )
        return self._path_result(proposal, descriptor.paths, descriptor.label)

    def _path_result(
        self,
        proposal: EvolutionProposal,
        paths: tuple[str, ...],
        label: str,
    ) -> ProposalValidation:
        offenders = check_patch_paths(paths, repo_root=self.repo_root, treeish=self.treeish)
        if offenders:
            return ProposalValidation(
                False,
                "PATH_NOT_ALLOWED",
                f"target paths are outside the Evolver candidate allowlist or unsafe: {offenders}",
                target_paths=paths,
                label=label,
            )
        return ProposalValidation(True, "ACCEPTED", "proposal passed target and path validation", paths, label)


class ProposalGenerator:
    """Turn development-only mined findings into conservative proposals."""

    _TARGET_BY_REASON = {
        "tool_execution_error": (EvolutionTargetLayer.TOOL_POLICY, "appworld.tool_policy"),
        "repeated_tool_behavior": (EvolutionTargetLayer.TOOL_POLICY, "appworld.tool_policy"),
        "verification_failure": (EvolutionTargetLayer.PROMPT, "appworld.prompt"),
        "finalization_or_submission_failure": (EvolutionTargetLayer.PROMPT, "appworld.prompt"),
        "no_convergence_before_budget": (EvolutionTargetLayer.PROMPT, "appworld.prompt"),
        "empty_response": (EvolutionTargetLayer.PROMPT, "appworld.prompt"),
        "no_tool_progress": (EvolutionTargetLayer.PROMPT, "appworld.prompt"),
    }

    def __init__(
        self,
        *,
        registry: ProposalTargetRegistry | None = None,
        evaluation_registry: EvaluationRegistry | None = None,
        min_repeated_skill_pattern: int = 3,
    ) -> None:
        self.targets = registry or default_target_registry()
        self.evaluation_registry = evaluation_registry
        self.min_repeated_skill_pattern = min_repeated_skill_pattern

    def generate(
        self,
        report: "FailureMiningReport",
        *,
        split: EvaluationSplit | str = EvaluationSplit.DEVELOPMENT,
        max_proposals: int | None = None,
    ) -> list[EvolutionProposal]:
        parsed = EvaluationSplit.parse(split)
        if parsed is not EvaluationSplit.DEVELOPMENT:
            raise HoldoutLeakError("Proposal Generator may consume development findings only")
        if self.evaluation_registry is not None:
            allowed = set(self.evaluation_registry.task_ids(parsed, consumer="optimizer"))
        else:
            allowed = None
        proposals: list[EvolutionProposal] = []
        seen_signatures: set[str] = set()
        for finding in report.findings:
            if allowed is not None and finding.task_id not in allowed:
                raise HoldoutLeakError(f"finding task {finding.task_id!r} is not in development")
            if finding.behavior_signature in seen_signatures or finding.failure_reason == "completed_without_failure":
                continue
            seen_signatures.add(finding.behavior_signature)
            layer, target_id = self._TARGET_BY_REASON.get(
                finding.failure_reason,
                (EvolutionTargetLayer.PROMPT, "appworld.prompt"),
            )
            proposal = EvolutionProposal(
                target_layer=layer,
                action=ProposalAction.MODIFY_EXISTING,
                target_id=target_id,
                root_cause=finding.failure_reason,
                evidence=tuple(item.to_dict() for item in finding.evidence)
                or ({"trace_id": finding.trace_id, "behavior_signature": finding.behavior_signature},),
                reason=(
                    f"modify the existing {layer.value} target for repeated behavior "
                    f"{finding.behavior_signature}"
                ),
                repeated_pattern_count=report.signature_counts.get(finding.behavior_signature, 1),
            )
            if EvolutionProposalValidator(self.targets).validate(proposal).accepted:
                proposals.append(proposal)
            if max_proposals is not None and len(proposals) >= max_proposals:
                break
        return proposals

    def create_new_skill(
        self,
        *,
        target_id: str,
        root_cause: str,
        evidence: Iterable[Any],
        reason: str,
        repeated_pattern_count: int,
    ) -> EvolutionProposal:
        """Explicit extension point; creation remains threshold-gated."""

        proposal = EvolutionProposal(
            target_layer=EvolutionTargetLayer.SKILL,
            action=ProposalAction.CREATE_NEW,
            target_id=target_id,
            root_cause=root_cause,
            evidence=tuple(evidence),
            reason=reason,
            repeated_pattern_count=repeated_pattern_count,
        )
        validation = EvolutionProposalValidator(
            self.targets,
            min_repeated_skill_pattern=self.min_repeated_skill_pattern,
        ).validate(proposal)
        if not validation.accepted:
            raise ValueError(f"{validation.code}: {validation.reason}")
        return proposal


def validate_proposal(
    proposal: EvolutionProposal,
    *,
    registry: ProposalTargetRegistry | None = None,
    repo_root: str | Path | None = None,
    treeish: str | None = None,
) -> ProposalValidation:
    return EvolutionProposalValidator(registry, repo_root=repo_root, treeish=treeish).validate(proposal)


__all__ = [
    "EvolutionProposal",
    "EvolutionProposalValidator",
    "EvolutionTargetLayer",
    "ProposalGenerator",
    "ProposalAction",
    "ProposalTargetRegistry",
    "ProposalValidation",
    "TargetDescriptor",
    "default_target_registry",
    "validate_proposal",
]
