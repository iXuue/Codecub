"""Deterministic instruction hierarchy for provider-bound agent context.

The resolver is intentionally a small value-oriented boundary.  It knows
about instruction provenance, scope and precedence, but it does not know
about Pico, Runtime, tools, storage, or model invocation.  Context assembly
consumes its result; it never re-interprets or re-orders the result.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum


class InstructionLayer(str, Enum):
    """Precedence from strongest to weakest."""

    RUNTIME_SAFETY = "runtime_safety"
    PROTECTED_RUNTIME = "protected_runtime"
    REPOSITORY = "repository"
    AGENT = "agent"
    TOOL = "tool"
    USER = "user"
    TASK = "task"

    @property
    def precedence(self):
        return {
            InstructionLayer.RUNTIME_SAFETY: 600,
            InstructionLayer.PROTECTED_RUNTIME: 500,
            InstructionLayer.REPOSITORY: 400,
            InstructionLayer.AGENT: 300,
            InstructionLayer.TOOL: 250,
            InstructionLayer.USER: 200,
            InstructionLayer.TASK: 100,
        }[self]


class InstructionScope(str, Enum):
    """Scopes that can be activated for one provider-bound request."""

    GLOBAL = "global"
    REPOSITORY = "repository"
    AGENT_ROLE = "agent-role"
    TOOL = "tool"
    TURN = "turn"
    RUN = "run"


_LAYER_ALIASES = {
    "runtime": InstructionLayer.RUNTIME_SAFETY,
    "runtime-safety": InstructionLayer.RUNTIME_SAFETY,
    "runtime_safety": InstructionLayer.RUNTIME_SAFETY,
    "safety": InstructionLayer.RUNTIME_SAFETY,
    "protected": InstructionLayer.PROTECTED_RUNTIME,
    "protected-runtime": InstructionLayer.PROTECTED_RUNTIME,
    "protected_runtime": InstructionLayer.PROTECTED_RUNTIME,
    "repo": InstructionLayer.REPOSITORY,
    "repository": InstructionLayer.REPOSITORY,
    "repository-instruction": InstructionLayer.REPOSITORY,
    "agent": InstructionLayer.AGENT,
    "agent-role": InstructionLayer.AGENT,
    "tool": InstructionLayer.TOOL,
    "tool-usage": InstructionLayer.TOOL,
    "user": InstructionLayer.USER,
    "user-instruction": InstructionLayer.USER,
    "user_instruction": InstructionLayer.USER,
    "task": InstructionLayer.TASK,
}
_SOURCE_ALIASES = {
    "runtime": "runtime_safety",
    "runtime-safety": "runtime_safety",
    "runtime_safety": "runtime_safety",
    "safety": "runtime_safety",
    "protected": "protected_runtime",
    "protected-runtime": "protected_runtime",
    "protected_runtime": "protected_runtime",
    "protected-constraint": "protected_runtime",
    "repo": "repository",
    "repository": "repository",
    "repository-instruction": "repository",
    "repository_instruction": "repository",
    "agent-role": "agent",
    "agent_role": "agent",
    "tool-usage": "tool",
    "tool_usage": "tool",
    "user_instruction": "user",
    "user-instruction": "user",
}
_SCOPE_ALIASES = {
    "global": InstructionScope.GLOBAL,
    "repository": InstructionScope.REPOSITORY,
    "repo": InstructionScope.REPOSITORY,
    "agent": InstructionScope.AGENT_ROLE,
    "agent-role": InstructionScope.AGENT_ROLE,
    "agent_role": InstructionScope.AGENT_ROLE,
    "tool": InstructionScope.TOOL,
    "turn": InstructionScope.TURN,
    "run": InstructionScope.RUN,
}


def _canonical_layer(value, source="", protected=False):
    if value is None or str(value).strip() == "":
        normalized_source = str(source or "").strip().lower().replace(" ", "-")
        if normalized_source in {"protected_runtime", "protected-runtime", "protected-constraint"}:
            return InstructionLayer.PROTECTED_RUNTIME
        if protected and not normalized_source:
            return InstructionLayer.PROTECTED_RUNTIME
        value = source or InstructionLayer.TASK.value
    if isinstance(value, InstructionLayer):
        return value
    text = str(value).strip().lower().replace(" ", "-")
    if text in _LAYER_ALIASES:
        return _LAYER_ALIASES[text]
    try:
        return InstructionLayer(text)
    except ValueError as exc:
        raise ValueError(f"unknown instruction layer: {value!r}") from exc


def _canonical_source(value, layer):
    text = str(value or "").strip().lower().replace(" ", "-")
    if text in _SOURCE_ALIASES:
        return _SOURCE_ALIASES[text]
    if text:
        return text
    return layer.value


def _canonical_scope(value):
    if isinstance(value, InstructionScope):
        return value
    text = str(value or InstructionScope.GLOBAL.value).strip().lower().replace(" ", "-")
    if text in _SCOPE_ALIASES:
        return _SCOPE_ALIASES[text]
    raise ValueError(f"unknown instruction scope: {value!r}")


def _normalized_content(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _derived_conflict(text):
    """Derive only obvious allow/deny action conflicts.

    This is deliberately conservative.  Ambiguous prose is retained instead
    of being guessed into a conflict.  Callers can always provide an explicit
    ``conflict_key`` and ``polarity`` for repository- or product-specific
    policy.
    """

    compact = re.sub(r"\s+", " ", str(text or "").strip().lower())
    action = r"(?:modify|edit|write|delete|change|touch|overwrite|remove)"
    negative = re.search(
        rf"\b(?:do not|don't|must not|never|not allowed to|forbidden to)\s+{action}\s+([^;\n]+)",
        compact,
    )
    if negative:
        target = re.sub(r"\s+", " ", negative.group(1)).strip(" .,:;")
        return f"action:{target}", "deny"
    positive = re.search(
        rf"\b(?:may|can|allowed to|is allowed to|should)\s+{action}\s+([^;\n]+)",
        compact,
    )
    if positive:
        target = re.sub(r"\s+", " ", positive.group(1)).strip(" .,:;")
        return f"action:{target}", "allow"
    return "", ""


@dataclass(frozen=True)
class Instruction:
    """One model-visible instruction with non-rendered provenance."""

    content: str
    source: str = ""
    layer: InstructionLayer | str | None = None
    priority: int | None = None
    scope: InstructionScope | str = InstructionScope.GLOBAL
    scope_id: str = ""
    protected: bool = False
    conflict_key: str = ""
    polarity: str = ""
    metadata: Mapping = field(default_factory=dict)
    id: str = ""

    def __post_init__(self):
        content = str(self.content or "").strip()
        if not content:
            raise ValueError("instruction content must not be empty")
        protected = bool(self.protected)
        layer = _canonical_layer(self.layer, self.source, protected)
        if layer == InstructionLayer.PROTECTED_RUNTIME:
            protected = True
        source = _canonical_source(self.source, layer)
        scope = _canonical_scope(self.scope)
        metadata = dict(self.metadata or {})
        conflict_key = str(self.conflict_key or metadata.get("conflict_key", "")).strip()
        polarity = str(self.polarity or metadata.get("polarity", "")).strip().lower()
        derived_key, derived_polarity = _derived_conflict(content)
        if not conflict_key:
            conflict_key = derived_key
        if not polarity:
            polarity = derived_polarity
        if protected and layer not in {
            InstructionLayer.RUNTIME_SAFETY,
            InstructionLayer.PROTECTED_RUNTIME,
        }:
            metadata.setdefault("protected_origin", layer.value)
        identity = self.id or _instruction_id(
            content,
            source,
            layer.value,
            scope.value,
            self.scope_id,
            protected,
            conflict_key,
            polarity,
            str(metadata.get("scope_path", ""))
            if layer == InstructionLayer.REPOSITORY
            else "",
        )
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "layer", layer)
        # Priority is derived from the fixed hierarchy.  Accepting the field
        # in the model makes the precedence explicit without allowing a
        # caller to silently override the safety ordering.
        object.__setattr__(self, "priority", layer.precedence)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "scope_id", str(self.scope_id or "").strip())
        object.__setattr__(self, "protected", protected)
        object.__setattr__(self, "conflict_key", conflict_key)
        object.__setattr__(self, "polarity", polarity)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "id", identity)

    @property
    def precedence(self):
        return self.layer.precedence

    def dedup_key(self):
        repository_scope = ""
        if self.layer == InstructionLayer.REPOSITORY:
            repository_scope = str(
                (self.metadata or {}).get("scope_path", "")
            ).strip().casefold()
        return (
            _normalized_content(self.content),
            self.source,
            self.layer.value,
            self.scope.value,
            self.scope_id,
            repository_scope,
            self.protected,
            self.conflict_key,
            self.polarity,
        )

    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "layer": self.layer.value,
            "priority": self.priority,
            "scope": self.scope.value,
            "scope_id": self.scope_id,
            "protected": self.protected,
            "conflict_key": self.conflict_key,
            "polarity": self.polarity,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_value(
        cls, value, *, default_source="task", default_layer=None, default_scope="global"
    ):
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            data = dict(value)
            if "content" not in data and "text" in data:
                data["content"] = data.pop("text")
            data.setdefault("source", default_source)
            if default_layer is not None:
                data.setdefault("layer", default_layer)
            data.setdefault("scope", default_scope)
            return cls(**data)
        return cls(
            content=str(value),
            source=default_source,
            layer=default_layer,
            scope=default_scope,
        )


def _instruction_id(
    content,
    source,
    layer,
    scope,
    scope_id,
    protected,
    conflict_key,
    polarity,
    repository_scope="",
):
    payload = json.dumps(
        {
            "content": content,
            "source": source,
            "layer": layer,
            "scope": scope,
            "scope_id": scope_id,
            "protected": protected,
            "conflict_key": conflict_key,
            "polarity": polarity,
            "repository_scope": repository_scope,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "instruction:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class InstructionConflict:
    key: str
    winner_id: str
    loser_id: str
    winner_layer: str
    loser_layer: str
    reason: str

    def to_dict(self):
        return {
            "key": self.key,
            "winner_id": self.winner_id,
            "loser_id": self.loser_id,
            "winner_layer": self.winner_layer,
            "loser_layer": self.loser_layer,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ResolvedInstructions:
    """Resolver output, including evidence for removed duplicates/conflicts."""

    instructions: tuple[Instruction, ...] = ()
    deduplicated_count: int = 0
    shadowed: tuple[Instruction, ...] = ()
    conflicts: tuple[InstructionConflict, ...] = ()
    input_count: int = 0
    active_count: int = 0

    @property
    def items(self):
        return self.instructions

    @property
    def protected_count(self):
        return sum(1 for item in self.instructions if item.protected)

    @property
    def shadowed_count(self):
        return len(self.shadowed)

    @property
    def conflict_count(self):
        return len(self.conflicts)

    @property
    def ids(self):
        return tuple(item.id for item in self.instructions)

    def render(self, *, include_task=True):
        items = self.instructions
        if not include_task:
            items = tuple(item for item in items if item.layer != InstructionLayer.TASK)
        if not items:
            return ""
        return "Instructions:\n" + "\n".join(f"- {item.content}" for item in items)

    def to_dict(self):
        return {
            "instructions": [item.to_dict() for item in self.instructions],
            "deduplicated_count": self.deduplicated_count,
            "protected_count": self.protected_count,
            "shadowed": [item.to_dict() for item in self.shadowed],
            "shadowed_count": self.shadowed_count,
            "conflicts": [item.to_dict() for item in self.conflicts],
            "input_count": self.input_count,
            "active_count": self.active_count,
        }


class InstructionResolver:
    """Resolve instruction values deterministically for one active scope."""

    def __init__(self, instructions: Iterable = ()):
        self.instructions = self._as_values(instructions)
        self.resolve_call_count = 0
        self.last_result: ResolvedInstructions | None = None

    def resolve(
        self,
        instructions: Iterable = (),
        *,
        agent_role="",
        repository_id="",
        turn_id="",
        run_id="",
        tool_name="",
        active_scopes=None,
    ):
        self.resolve_call_count += 1
        values = [*self.instructions, *self._as_values(instructions)]
        normalized = []
        for value in values:
            try:
                normalized.append(Instruction.from_value(value))
            except (TypeError, ValueError):
                # A malformed optional source is not a provider instruction;
                # callers still get deterministic output for valid sources.
                continue
        active = [
            item
            for item in normalized
            if self._scope_active(
                item,
                agent_role=agent_role,
                repository_id=repository_id,
                turn_id=turn_id,
                run_id=run_id,
                tool_name=tool_name,
                active_scopes=active_scopes,
            )
        ]
        deduped = []
        seen = set()
        deduplicated_count = 0
        for item in active:
            key = item.dedup_key()
            if key in seen:
                deduplicated_count += 1
                continue
            seen.add(key)
            deduped.append(item)

        winners = list(deduped)
        shadowed = []
        conflicts = []
        by_conflict = {}
        for item in deduped:
            if not item.conflict_key or not item.polarity:
                continue
            # An explicit/derived conflict key denotes one policy subject
            # across active scopes.  Scope controls eligibility; it must not
            # let a lower-layer global instruction evade a protected turn
            # constraint (or vice versa).
            group_key = item.conflict_key
            by_conflict.setdefault(group_key, []).append(item)
        for group_key, candidates in by_conflict.items():
            polarities = {item.polarity for item in candidates}
            if len(polarities) < 2:
                continue
            winner = max(candidates, key=self._winner_key)
            for item in candidates:
                if item.id == winner.id:
                    continue
                if item in winners:
                    winners.remove(item)
                shadowed.append(item)
                conflicts.append(
                    InstructionConflict(
                        key=group_key,
                        winner_id=winner.id,
                        loser_id=item.id,
                        winner_layer=winner.layer.value,
                        loser_layer=item.layer.value,
                        reason="higher_precedence_or_protected_instruction",
                    )
                )

        winners.sort(key=self._sort_key)
        shadowed.sort(key=self._sort_key)
        conflicts.sort(key=lambda item: (item.key, item.winner_id, item.loser_id))
        result = ResolvedInstructions(
            instructions=tuple(winners),
            deduplicated_count=deduplicated_count,
            shadowed=tuple(shadowed),
            conflicts=tuple(conflicts),
            input_count=len(values),
            active_count=len(active),
        )
        self.last_result = result
        return result

    @staticmethod
    def _as_values(value):
        if value is None:
            return ()
        if isinstance(value, (Instruction, Mapping, str)):
            return (value,)
        return tuple(value)

    @staticmethod
    def _scope_active(item, *, agent_role, repository_id, turn_id, run_id, tool_name, active_scopes):
        if active_scopes is not None:
            if isinstance(active_scopes, str):
                active_scopes = (active_scopes,)
            allowed = {
                str(value.value if isinstance(value, Enum) else value)
                for value in active_scopes
            }
            if item.scope.value not in allowed and item.scope != InstructionScope.GLOBAL:
                return False
        expected = {
            InstructionScope.GLOBAL: "",
            InstructionScope.REPOSITORY: repository_id,
            InstructionScope.AGENT_ROLE: agent_role,
            InstructionScope.TOOL: tool_name,
            InstructionScope.TURN: turn_id,
            InstructionScope.RUN: run_id,
        }[item.scope]
        return not item.scope_id or str(expected or "").strip() == item.scope_id

    @staticmethod
    def _winner_key(item):
        specificity_depth, specificity_path = InstructionResolver._repository_specificity(item)
        return (
            item.precedence,
            int(item.protected),
            specificity_depth,
            specificity_path,
            item.source,
            item.scope.value,
            item.scope_id,
            item.id,
        )

    @staticmethod
    def _sort_key(item):
        specificity_depth, specificity_path = InstructionResolver._repository_specificity(item)
        return (
            -item.precedence,
            -int(item.protected),
            specificity_depth,
            specificity_path,
            item.layer.value,
            item.source,
            item.scope.value,
            item.scope_id,
            item.conflict_key,
            item.polarity,
            item.id,
        )

    @staticmethod
    def _repository_specificity(item):
        """Return deterministic repository-layer specificity for one item."""

        if item.layer != InstructionLayer.REPOSITORY:
            return 0, ""
        metadata = item.metadata or {}
        try:
            depth = max(0, int(metadata.get("specificity_depth", 0) or 0))
        except (TypeError, ValueError):
            depth = 0
        path = str(metadata.get("scope_path", "") or "").strip().casefold()
        return depth, path


__all__ = [
    "Instruction",
    "InstructionConflict",
    "InstructionLayer",
    "InstructionResolver",
    "InstructionScope",
    "ResolvedInstructions",
]
