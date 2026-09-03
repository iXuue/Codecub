"""Read-only validation for provider-bound assembled context.

The validator is deliberately narrower than :mod:`codecub.context_assembler`.
It consumes the final ``AssembledContext`` and its declared validation inputs;
it never retrieves, compresses, trims, mutates, or rebuilds context.  Callers
may use the returned action to request one bounded re-assembly attempt, but
that retry policy remains outside this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field


VALID = "VALID"
VALID_WITH_FALLBACK = "VALID_WITH_FALLBACK"
INVALID = "INVALID"

ALLOW = "ALLOW"
RETRY_ASSEMBLY = "RETRY_ASSEMBLY"
REJECT = "REJECT"

HARD = "HARD"
SOFT = "SOFT"

_UNSET = object()


@dataclass(frozen=True)
class ValidationCheck:
    """One deterministic invariant result without raw context content."""

    name: str
    ok: bool | None
    required: bool = True
    severity: str = SOFT
    reason_code: str = ""
    details: Mapping = field(default_factory=dict)

    def to_dict(self):
        return {
            "name": self.name,
            "ok": self.ok,
            "required": self.required,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class ContextValidationEvidence:
    """Structured evidence emitted for one validation attempt."""

    protocol: str = ""
    status: str = INVALID
    action: str = REJECT
    budget_ok: bool | None = None
    freshness_ok: bool | None = None
    protected_constraints_present: bool | None = None
    working_state_present: bool | None = None
    retrieval_evidence_valid: bool | None = None
    memory_evidence_valid: bool | None = None
    provider_limit_ok: bool | None = None
    native_continuity_ok: bool | None = None
    instruction_integrity_ok: bool | None = None
    instruction_count: int = 0
    instruction_shadowed_count: int = 0
    instruction_conflict_count: int = 0
    required_source_presence: Mapping = field(default_factory=dict)
    optional_source_presence: Mapping = field(default_factory=dict)
    raw_tokens: int | None = None
    final_tokens: int | None = None
    budget: int | None = None
    provider_limit: int | None = None
    fallback_used: bool = False
    fallback_reason: str = ""
    failed_checks: tuple[str, ...] = ()
    hard_failures: tuple[str, ...] = ()
    soft_failures: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    checks: tuple[ValidationCheck, ...] = ()

    @property
    def valid(self):
        return self.status in {VALID, VALID_WITH_FALLBACK}

    @property
    def ok(self):
        return self.valid

    def to_dict(self):
        return {
            "protocol": self.protocol,
            "status": self.status,
            "action": self.action,
            "valid": self.valid,
            "budget_ok": self.budget_ok,
            "freshness_ok": self.freshness_ok,
            "protected_constraints_present": self.protected_constraints_present,
            "working_state_present": self.working_state_present,
            "retrieval_evidence_valid": self.retrieval_evidence_valid,
            "memory_evidence_valid": self.memory_evidence_valid,
            "provider_limit_ok": self.provider_limit_ok,
            "native_continuity_ok": self.native_continuity_ok,
            "instruction_integrity_ok": self.instruction_integrity_ok,
            "instruction_count": self.instruction_count,
            "instruction_shadowed_count": self.instruction_shadowed_count,
            "instruction_conflict_count": self.instruction_conflict_count,
            "required_source_presence": dict(self.required_source_presence or {}),
            "optional_source_presence": dict(self.optional_source_presence or {}),
            "raw_tokens": self.raw_tokens,
            "final_tokens": self.final_tokens,
            "budget": self.budget,
            "provider_limit": self.provider_limit,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "failed_checks": list(self.failed_checks),
            "hard_failures": list(self.hard_failures),
            "soft_failures": list(self.soft_failures),
            "limitations": list(self.limitations),
            "checks": [check.to_dict() for check in self.checks],
        }

    def __getitem__(self, key):
        return self.to_dict()[key]


@dataclass(frozen=True)
class ContextValidationResult:
    """Validation outcome plus evidence and the caller-facing action."""

    status: str
    action: str
    evidence: ContextValidationEvidence
    error: str = ""

    @property
    def valid(self):
        return self.status in {VALID, VALID_WITH_FALLBACK}

    @property
    def ok(self):
        return self.valid

    @property
    def allowed(self):
        return self.action == ALLOW and self.valid

    @property
    def failed_checks(self):
        return self.evidence.failed_checks

    def __bool__(self):
        return self.valid

    def __getattr__(self, name):
        # Convenience for narrow callers that historically treated a result
        # as the evidence object (result.budget_ok, result.native_continuity_ok).
        evidence = object.__getattribute__(self, "evidence")
        if hasattr(evidence, name):
            return getattr(evidence, name)
        raise AttributeError(name)

    def to_dict(self):
        return {
            "status": self.status,
            "action": self.action,
            "valid": self.valid,
            "allowed": self.allowed,
            "error": self.error,
            "evidence": self.evidence.to_dict(),
        }

    def __getitem__(self, key):
        return self.to_dict()[key]


class ContextValidationError(RuntimeError):
    """Raised only by an optional caller-side policy, never by validation."""


class ContextValidator:
    """Deterministically validate one final provider-bound context.

    Dependencies are value-like: an optional token counter and an optional
    per-path freshness checker.  In particular, this class does not accept or
    retain a Runtime/Pico object.
    """

    def __init__(
        self,
        token_counter=None,
        freshness_checker=None,
        workspace_root=None,
        policy=None,
        validation_policy=None,
        max_validation_attempts=1,
    ):
        self.token_counter = token_counter
        self.freshness_checker = freshness_checker
        self.workspace_root = workspace_root
        self.policy = dict(policy or validation_policy or {})
        self.max_validation_attempts = max(0, int(max_validation_attempts or 0))
        self.validate_call_count = 0
        self.validation_count = 0
        self.last_result = None
        self.last_evidence = None

    def validate(self, context, *, protocol=None, policy=None, requirements=None):
        """Return a structured result for the exact context that will be sent.

        ``policy`` and ``requirements`` are narrow mappings.  Context-local
        requirements take precedence over constructor defaults, and explicit
        call requirements take precedence over both.
        """

        self.validate_call_count += 1
        self.validation_count = self.validate_call_count
        metadata = dict(getattr(context, "metadata", {}) or {})
        merged = dict(self.policy)
        for candidate in (
            getattr(context, "validation_requirements", None),
            metadata.get("validation_input"),
            metadata.get("validation_requirements"),
            policy,
            requirements,
        ):
            if isinstance(candidate, Mapping):
                merged.update(dict(candidate))

        selected_protocol = str(protocol or getattr(context, "protocol", "") or "")
        messages = list(getattr(context, "messages", []) or [])
        prompt = str(getattr(context, "prompt", "") or "")
        model_input = messages if selected_protocol == "native_tools" else prompt
        segments = list(getattr(context, "segments", []) or [])
        checks = []
        limitations = []

        final_tokens = self._final_tokens(metadata, model_input, selected_protocol)
        raw_tokens = self._raw_tokens(metadata)
        budget = self._budget_limit(metadata, merged)
        provider_limit = self._provider_limit(metadata, merged)

        budget_ok, budget_details = self._check_budget(
            metadata, final_tokens, budget
        )
        checks.append(
            ValidationCheck(
                "budget",
                budget_ok,
                required=budget is not None or metadata.get("prompt_over_budget") is not None,
                severity=HARD,
                reason_code="BUDGET_EXCEEDED" if budget_ok is False else "",
                details=budget_details,
            )
        )

        provider_ok, provider_details = self._check_provider_limit(
            final_tokens, provider_limit
        )
        checks.append(
            ValidationCheck(
                "provider_limit",
                provider_ok,
                required=provider_limit is not None,
                severity=HARD,
                reason_code=(
                    "PROVIDER_CONTEXT_LIMIT_EXCEEDED" if provider_ok is False else ""
                ),
                details=provider_details,
            )
        )

        requirements_list = self._as_names(merged.get("required_sources", ()))
        optional_list = self._as_names(merged.get("optional_sources", ()))
        if merged.get("require_user_task", True) and "user_task" not in requirements_list:
            requirements_list.insert(0, "user_task")
        working_required = bool(
            merged.get("working_state_required", False)
            or "working_state" in requirements_list
        )
        working_present = self._working_state_present(
            context, model_input, segments
        )
        if working_required and "working_state" not in requirements_list:
            requirements_list.append("working_state")
        required_presence = {}
        for source in requirements_list:
            if source == "working_state":
                present = working_present
            elif source == "protected_constraints":
                present = True  # checked structurally below
            else:
                present = self._source_present(
                    source, context, model_input, segments, metadata, merged
                )
            required_presence[source] = bool(present)
            if not present and source != "protected_constraints":
                checks.append(
                    ValidationCheck(
                        f"required_source:{source}",
                        False,
                        required=True,
                        severity=HARD,
                        reason_code="REQUIRED_SOURCE_MISSING",
                        details={"source": source},
                    )
                )
        optional_presence = {}
        for source in optional_list:
            if source == "working_state":
                present = working_present
            else:
                present = self._source_present(
                    source, context, model_input, segments, metadata, merged
                )
            optional_presence[source] = bool(present)

        working_ok = working_present if working_required else None
        checks.append(
            ValidationCheck(
                "working_state",
                working_ok,
                required=working_required,
                severity=HARD,
                reason_code="WORKING_STATE_MISSING" if working_ok is False else "",
                details={"required": working_required, "present": working_present},
            )
        )

        protected = self._protected_constraints(merged, metadata)
        protected_ok, protected_details, protected_limitation = self._check_protected(
            context, model_input, segments, protected, merged
        )
        if protected_limitation:
            limitations.append(protected_limitation)
        checks.append(
            ValidationCheck(
                "protected_constraints",
                protected_ok,
                required=bool(protected),
                severity=HARD,
                reason_code=(
                    "PROTECTED_CONSTRAINT_MISSING" if protected_ok is False else ""
                ),
                details=protected_details,
            )
        )

        instruction_ok, instruction_details = self._check_instruction_integrity(
            context, model_input, segments, merged
        )
        resolved = getattr(context, "resolved_instructions", None)
        if resolved is None:
            resolved = merged.get("resolved_instructions")
        checks.append(
            ValidationCheck(
                "instruction_integrity",
                instruction_ok,
                required=resolved is not None,
                severity=HARD,
                reason_code=(
                    "INSTRUCTION_INTEGRITY_INVALID" if instruction_ok is False else ""
                ),
                details=instruction_details,
            )
        )

        freshness_ok, freshness_details = self._check_freshness(
            merged, metadata
        )
        if freshness_details.pop("limitation", ""):
            limitations.append("freshness_checker_unavailable")
        checks.append(
            ValidationCheck(
                "freshness",
                freshness_ok,
                required=bool(merged.get("freshness_required") or merged.get("freshness_entries")),
                severity=HARD,
                reason_code="STALE_REQUIRED_CONTEXT" if freshness_ok is False else "",
                details=freshness_details,
            )
        )

        retrieval_ok, retrieval_details = self._check_declared_evidence(
            "retrieval", merged, metadata, segments, model_input
        )
        checks.append(
            ValidationCheck(
                "retrieval_evidence",
                retrieval_ok,
                required=bool(
                    merged.get("retrieval_required")
                    or merged.get("retrieval_evidence")
                    or "retrieval" in requirements_list
                ),
                severity=HARD,
                reason_code=(
                    "RETRIEVAL_EVIDENCE_MISSING" if retrieval_ok is False else ""
                ),
                details=retrieval_details,
            )
        )

        memory_ok, memory_details = self._check_memory(
            merged, metadata, model_input
        )
        checks.append(
            ValidationCheck(
                "memory_evidence",
                memory_ok,
                required=bool(
                    merged.get("memory_required") or "memory" in requirements_list
                ),
                severity=HARD if memory_details.get("required") else SOFT,
                reason_code="MEMORY_EVIDENCE_MISSING" if memory_ok is False else "",
                details=memory_details,
            )
        )

        native_ok, native_details = self._check_native(
            selected_protocol, messages, context, model_input, merged
        )
        checks.append(
            ValidationCheck(
                "native_continuity",
                native_ok,
                required=selected_protocol == "native_tools",
                severity=HARD,
                reason_code=(
                    "NATIVE_CONTINUITY_INVALID" if native_ok is False else ""
                ),
                details=native_details,
            )
        )

        legacy_ok, legacy_details = self._check_legacy(
            selected_protocol, prompt, model_input
        )
        checks.append(
            ValidationCheck(
                "legacy_input",
                legacy_ok,
                required=selected_protocol in {"legacy_text", "legacy_stream"},
                severity=HARD,
                reason_code="LEGACY_CONTEXT_EMPTY" if legacy_ok is False else "",
                details=legacy_details,
            )
        )

        required_failures = [
            check
            for check in checks
            if check.required and check.ok is False
        ]
        hard_failures = [
            check.reason_code or check.name
            for check in required_failures
            if check.severity == HARD
        ]
        soft_failures = [
            check.reason_code or check.name
            for check in checks
            if check.ok is False and check.severity == SOFT
        ]
        failed_checks = [check.name for check in checks if check.ok is False]

        fallback_used, fallback_reason = self._fallback_evidence(metadata, merged)
        if soft_failures:
            fallback_used = True
            fallback_reason = fallback_reason or "OPTIONAL_CONTEXT_FALLBACK"

        retryable_codes = {
            "BUDGET_EXCEEDED",
            "PROVIDER_CONTEXT_LIMIT_EXCEEDED",
            "STALE_REQUIRED_CONTEXT",
        }
        retryable = bool(hard_failures) and all(
            code in retryable_codes for code in hard_failures
        )
        if hard_failures:
            status = INVALID
            action = RETRY_ASSEMBLY if retryable else REJECT
        else:
            status = VALID_WITH_FALLBACK if fallback_used else VALID
            action = ALLOW
        error = "; ".join(hard_failures or soft_failures)

        evidence = ContextValidationEvidence(
            protocol=selected_protocol,
            status=status,
            action=action,
            budget_ok=budget_ok,
            freshness_ok=freshness_ok,
            protected_constraints_present=protected_ok,
            working_state_present=working_ok,
            retrieval_evidence_valid=retrieval_ok,
            memory_evidence_valid=memory_ok,
            provider_limit_ok=provider_ok,
            native_continuity_ok=native_ok,
            instruction_integrity_ok=instruction_ok,
            instruction_count=int(instruction_details.get("instruction_count", 0)),
            instruction_shadowed_count=int(
                instruction_details.get("shadowed_count", 0)
            ),
            instruction_conflict_count=int(
                instruction_details.get("conflict_count", 0)
            ),
            required_source_presence=required_presence,
            optional_source_presence=optional_presence,
            raw_tokens=raw_tokens,
            final_tokens=final_tokens,
            budget=budget,
            provider_limit=provider_limit,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            failed_checks=tuple(failed_checks),
            hard_failures=tuple(hard_failures),
            soft_failures=tuple(soft_failures),
            limitations=tuple(limitations),
            checks=tuple(checks),
        )
        result = ContextValidationResult(status, action, evidence, error)
        self.last_evidence = evidence
        self.last_result = result
        return result

    @staticmethod
    def _as_names(value):
        if isinstance(value, str):
            return [value]
        return [str(item) for item in (value or ()) if str(item)]

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _final_tokens(self, metadata, model_input, protocol):
        keys = (
            (
                "final_provider_bound_tokens",
                "provider_bound_prompt_tokens",
                "compiled_context_tokens",
            )
            if protocol == "native_tools"
            else ("compiled_context_tokens", "estimated_prompt_tokens", "prompt_tokens")
        )
        for key in keys:
            value = self._number(metadata.get(key))
            if value is not None:
                return value
        if str(metadata.get("budget_mode", "")) == "char":
            value = self._number(metadata.get("prompt_chars"))
            if value is not None:
                return value
        if self.token_counter is not None:
            try:
                rendered = (
                    json.dumps(model_input, sort_keys=True, ensure_ascii=False, default=str)
                    if protocol == "native_tools"
                    else str(model_input)
                )
                return max(0, int(self.token_counter.count(rendered)))
            except Exception:
                pass
        return None

    def _raw_tokens(self, metadata):
        for key in ("candidate_context_tokens", "raw_model_visible_tokens", "raw_history_tokens"):
            value = self._number(metadata.get(key))
            if value is not None:
                return value
        return None

    def _budget_limit(self, metadata, requirements):
        # The legacy compiler can run without a tokenizer.  In that mode its
        # ``compiled_context_tokens`` field is actually a character count,
        # while ``usable_input_budget`` still comes from the token-shaped
        # ContextBudget configuration.  Prefer the real character budget from
        # the legacy assembly metadata so validation does not compare unlike
        # units.  Explicit requirements always win, which keeps focused
        # integrations able to impose a provider/compiler budget directly.
        for key in ("compiler_budget", "budget"):
            value = self._number(requirements.get(key))
            if value is not None:
                return value
            value = self._number(metadata.get(key))
            if value is not None:
                return value
        unit = str(
            metadata.get("validation_budget_unit")
            or metadata.get("budget_unit")
            or metadata.get("budget_mode")
            or ""
        ).lower()
        if unit in {"char", "chars", "character", "characters"}:
            compiler_budget_keys = (
                ("validation_budget", "usable_input_budget", "prompt_budget_chars", "prompt_budget")
                if metadata.get("compiler") == "context_compiler"
                else ("validation_budget", "prompt_budget_chars", "prompt_budget")
            )
            for key in compiler_budget_keys:
                value = self._number(requirements.get(key))
                if value is not None:
                    return value
                value = self._number(metadata.get(key))
                if value is not None:
                    return value
        for key in ("usable_input_budget", "prompt_budget", "prompt_budget_chars"):
            value = self._number(requirements.get(key))
            if value is not None:
                return value
            value = self._number(metadata.get(key))
            if value is not None:
                return value
        return None

    def _provider_limit(self, metadata, requirements):
        for key in ("provider_context_limit", "provider_hard_limit"):
            value = self._number(requirements.get(key))
            if value is not None:
                return value
            value = self._number(metadata.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _check_budget(metadata, final_tokens, budget):
        if metadata.get("prompt_over_budget") is True:
            return False, {"source": "prompt_over_budget", "final_tokens": final_tokens, "budget": budget}
        if budget is None or final_tokens is None:
            return None, {"source": "unavailable", "final_tokens": final_tokens, "budget": budget}
        if (
            str(metadata.get("compiler", "")) == "context_compiler"
            and str(metadata.get("budget_source", "")) == "fallback"
            and final_tokens > budget
        ):
            return None, {
                "source": "conservative_fallback_budget",
                "final_tokens": final_tokens,
                "budget": budget,
                "overflow_tokens": final_tokens - budget,
            }
        return final_tokens <= budget, {
            "source": "compiler_or_legacy_budget",
            "final_tokens": final_tokens,
            "budget": budget,
            "overflow_tokens": max(0, final_tokens - budget),
        }

    @staticmethod
    def _check_provider_limit(final_tokens, provider_limit):
        if provider_limit is None or final_tokens is None:
            return None, {"final_tokens": final_tokens, "provider_limit": provider_limit}
        return final_tokens <= provider_limit, {
            "final_tokens": final_tokens,
            "provider_limit": provider_limit,
            "overflow_tokens": max(0, final_tokens - provider_limit),
        }

    @staticmethod
    def _input_text(model_input):
        if isinstance(model_input, str):
            return model_input
        return json.dumps(model_input, sort_keys=True, ensure_ascii=False, default=str)

    def _working_state_present(self, context, model_input, segments):
        compiled = getattr(context, "compiled_context", None)
        state = getattr(compiled, "working_state", None) if compiled is not None else None
        if state is None:
            return False
        state_text = ""
        try:
            state_text = str(state.to_text() or "").strip()
        except Exception:
            state_text = ""
        rendered = self._input_text(model_input)
        return bool(state_text and (state_text in rendered or "Working State:" in rendered))

    def _source_present(self, source, context, model_input, segments, metadata, requirements):
        if source == "user_task":
            return bool(self._input_text(model_input).strip())
        if source == "working_state":
            return self._working_state_present(context, model_input, segments)
        if source in {"memory", "memory_layer"}:
            return bool(metadata.get("memory_layer_rendered") and metadata.get("memory_tokens", 0) > 0)
        if source in {"retrieval", "retrieval_evidence"}:
            return self._check_declared_evidence(
                "retrieval", requirements, metadata, segments, model_input
            )[0]
        if source == "protected_constraints":
            return bool(requirements.get("protected_constraints"))
        declared = (requirements.get("source_presence") or {}).get(source, _UNSET)
        if declared is not _UNSET:
            return bool(declared)
        marker = str(source)
        return any(
            marker == str(getattr(segment, "key", ""))
            or marker == str((getattr(segment, "provenance", {}) or {}).get("source", ""))
            for segment in segments
        )

    @staticmethod
    def _protected_constraints(requirements, metadata):
        value = requirements.get("protected_constraints", _UNSET)
        if value is _UNSET:
            value = metadata.get("protected_constraints", ())
        if value is _UNSET:
            value = ()
        if isinstance(value, str):
            value = (value,)
        return tuple(value or ())

    def _check_instruction_integrity(self, context, model_input, segments, requirements):
        resolved = getattr(context, "resolved_instructions", None)
        if resolved is None:
            resolved = requirements.get("resolved_instructions", _UNSET)
        if resolved is _UNSET or resolved is None:
            return None, {
                "instruction_count": 0,
                "shadowed_count": 0,
                "conflict_count": 0,
            }
        instructions = tuple(getattr(resolved, "instructions", ()) or ())
        expected_ids = requirements.get("instruction_integrity_ids", _UNSET)
        if expected_ids is not _UNSET:
            expected_ids = {str(item) for item in expected_ids or ()}
            instructions = tuple(
                item for item in instructions if str(getattr(item, "id", "")) in expected_ids
            )
        segment_by_id = {}
        for segment in segments:
            provenance = getattr(segment, "provenance", {}) or {}
            instruction_id = str(provenance.get("instruction_id", "") or "")
            if instruction_id:
                segment_by_id[instruction_id] = segment
        visible_texts = self._visible_instruction_texts(
            model_input, getattr(context, "protocol", "")
        )
        missing = []
        malformed = []
        required_protected = set(
            str(item)
            for item in requirements.get("required_protected_instruction_ids", ()) or ()
        )
        for item in instructions:
            instruction_id = str(getattr(item, "id", "") or "")
            segment = segment_by_id.get(instruction_id)
            if segment is None:
                missing.append(instruction_id)
                continue
            provenance = getattr(segment, "provenance", {}) or {}
            if str(getattr(segment, "kind", "")) != "instruction":
                malformed.append(instruction_id)
                continue
            if not all(
                str(provenance.get(field, "")).strip()
                for field in ("instruction_id", "source", "layer", "scope")
            ):
                malformed.append(instruction_id)
                continue
            expected_provenance = {
                "instruction_id": instruction_id,
                "source": str(getattr(item, "source", "")),
                "layer": str(
                    getattr(getattr(item, "layer", None), "value", getattr(item, "layer", ""))
                ),
                "scope": str(
                    getattr(getattr(item, "scope", None), "value", getattr(item, "scope", ""))
                ),
                "scope_id": str(getattr(item, "scope_id", "") or ""),
                "protected": bool(getattr(item, "protected", False)),
            }
            instruction_metadata = getattr(item, "metadata", {}) or {}
            for field_name in (
                "source_kind",
                "source_path",
                "scope_path",
                "repository_root",
                "specificity_depth",
                "template_filename",
                "file_freshness",
            ):
                if field_name in instruction_metadata:
                    expected_provenance[field_name] = instruction_metadata[field_name]
            if any(
                str(provenance.get(field, "")) != str(value)
                for field, value in expected_provenance.items()
            ):
                malformed.append(instruction_id)
                continue
            content = str(getattr(item, "content", "") or "").strip()
            if content and not any(content in text for text in visible_texts):
                missing.append(instruction_id)
        missing_protected = sorted(required_protected.intersection(missing))
        ok = not missing and not malformed
        return ok, {
            "instruction_count": len(instructions),
            "present_count": len(instructions) - len(set(missing)),
            "missing_ids": tuple(sorted(set(missing))),
            "missing_protected_ids": tuple(missing_protected),
            "malformed_ids": tuple(sorted(set(malformed))),
            "shadowed_count": int(getattr(resolved, "shadowed_count", 0)),
            "conflict_count": int(getattr(resolved, "conflict_count", 0)),
        }

    @staticmethod
    def _visible_instruction_texts(model_input, protocol):
        if str(protocol) != "native_tools":
            return (str(model_input),)
        texts = []
        for message in model_input if isinstance(model_input, list) else ():
            if not isinstance(message, Mapping):
                continue
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, Mapping) and item.get("text") is not None
                )
        return tuple(texts) or (ContextValidator._input_text(model_input),)

    @staticmethod
    def _constraint_id(value):
        if isinstance(value, Mapping):
            if value.get("id"):
                return str(value["id"])
            value = value.get("text", "")
        return hashlib.sha256(str(value).strip().encode("utf-8")).hexdigest()[:16]

    def _check_protected(
        self, context, model_input, segments, constraints, requirements=None
    ):
        if not constraints:
            return True, {"required_count": 0, "present_count": 0}, ""
        protected_keys = set()
        requirements = requirements or getattr(context, "validation_requirements", {}) or {}
        metadata = getattr(context, "metadata", {}) or {}
        protected_keys.update(str(key) for key in requirements.get("protected_segment_keys", ()) or ())
        protected_keys.update(str(key) for key in metadata.get("protected_segment_keys", ()) or ())
        structured_segments = [
            segment
            for segment in segments
            if str(getattr(segment, "key", "")) in protected_keys
            or str(getattr(segment, "key", "")).startswith("pinned:runtime-constraints")
            or bool((getattr(segment, "provenance", {}) or {}).get("protected"))
        ]
        structured_text = "\n".join(
            str(getattr(segment, "text", "") or "") for segment in structured_segments
        )
        rendered = self._input_text(model_input)
        present_ids = []
        missing_ids = []
        for constraint in constraints:
            constraint_id = self._constraint_id(constraint)
            text = str(constraint.get("text", "") if isinstance(constraint, Mapping) else constraint).strip()
            if text and text in structured_text:
                present_ids.append(constraint_id)
            elif text and text in rendered:
                present_ids.append(constraint_id)
            else:
                missing_ids.append(constraint_id)
        limitation = "" if structured_segments else "protected_constraint_text_fallback"
        return not missing_ids, {
            "required_count": len(constraints),
            "present_count": len(present_ids),
            "missing_ids": missing_ids,
            "present_ids": present_ids,
            "structured_segment_keys": [
                str(getattr(segment, "key", "")) for segment in structured_segments
            ],
        }, limitation

    def _check_freshness(self, requirements, metadata):
        entries = requirements.get("freshness_entries")
        if entries is None:
            entries = metadata.get("freshness_entries")
        if entries is None and requirements.get("freshness_required"):
            entries = (metadata.get("inspected_evidence") or {}).get("entries", [])
        entries = list(entries or [])
        if not entries:
            return None, {"declared_count": 0, "fresh_count": 0, "stale_count": 0}
        stale = []
        fresh = []
        limitation = ""
        for entry in entries:
            if not isinstance(entry, Mapping):
                stale.append("invalid_entry")
                continue
            path = str(entry.get("path", ""))
            expected = str(
                entry.get("freshness")
                or entry.get("source_hash")
                or entry.get("file_version")
                or ""
            )
            status = str(entry.get("status", "") or "").lower()
            if status in {"stale", "missing", "stale_read"}:
                stale.append(path or "unknown")
                continue
            actual = entry.get("current_freshness")
            if actual is None and path:
                if self.freshness_checker is not None:
                    try:
                        actual = self.freshness_checker(path)
                    except Exception:
                        actual = None
                elif self.workspace_root is not None:
                    try:
                        from .memory import file_freshness

                        actual = file_freshness(path, self.workspace_root)
                    except Exception:
                        actual = None
            if expected and actual is None:
                limitation = "freshness_checker_unavailable"
                fresh.append(path or "metadata_only")
            elif expected and str(actual) != expected:
                stale.append(path or "unknown")
            else:
                fresh.append(path or "metadata_only")
        return not stale, {
            "declared_count": len(entries),
            "fresh_count": len(fresh),
            "stale_count": len(stale),
            "stale_paths": stale,
            "limitation": limitation,
        }

    def _check_declared_evidence(self, kind, requirements, metadata, segments, model_input):
        entries = requirements.get(f"{kind}_evidence")
        required = bool(
            requirements.get(f"{kind}_required")
            or kind in self._as_names(requirements.get("required_sources", ()))
        )
        if entries is None:
            if not required:
                return None, {"declared_count": 0, "present_count": 0, "required": False}
            entries = metadata.get(f"{kind}_evidence") or []
        entries = list(entries or [])
        if not entries:
            return (False if required else None), {
                "declared_count": 0,
                "present_count": 0,
                "required": required,
            }
        rendered = self._input_text(model_input)
        present = 0
        missing = []
        stale_handled = 0
        for entry in entries:
            if isinstance(entry, Mapping):
                identifier = str(
                    entry.get("id")
                    or entry.get("record_id")
                    or entry.get("marker")
                    or entry.get("key")
                    or entry.get("path")
                    or ""
                )
                status = str(entry.get("status", "") or "").lower()
            else:
                identifier = str(entry)
                status = ""
            structured = any(
                identifier
                and (
                    identifier == str(getattr(segment, "key", ""))
                    or identifier == str((getattr(segment, "provenance", {}) or {}).get("record_id", ""))
                )
                for segment in segments
            )
            stale_ok = True
            if status in {"stale", "missing", "superseded"}:
                stale_ok = bool(
                    entry.get("stale_handled")
                    or "STALE—REVALIDATE" in rendered
                    or "STALE-REVALIDATE" in rendered
                    or "may have moved or been deleted" in rendered
                )
                if stale_ok:
                    stale_handled += 1
            if identifier and (structured or identifier in rendered) and stale_ok:
                present += 1
            else:
                missing.append(identifier or "unknown")
        return not missing, {
            "declared_count": len(entries),
            "present_count": present,
            "missing": missing,
            "stale_handled_count": stale_handled,
            "required": required,
        }

    @staticmethod
    def _check_memory(requirements, metadata, model_input):
        required = bool(requirements.get("memory_required"))
        rendered = bool(metadata.get("memory_layer_rendered") and metadata.get("memory_tokens", 0) > 0)
        if not required:
            return None, {
                "required": False,
                "rendered": rendered,
                "stale_count": int(metadata.get("memory_stale_count", 0) or 0),
            }
        return rendered, {
            "required": True,
            "rendered": rendered,
            "stale_count": int(metadata.get("memory_stale_count", 0) or 0),
        }

    def _check_native(self, protocol, messages, context, model_input, requirements):
        if protocol != "native_tools":
            return None, {"required": False}
        if not isinstance(messages, list) or not messages:
            return False, {"reason": "messages_empty_or_not_list"}
        pending = set()
        invalid = []
        assistant_calls = 0
        tool_results = 0
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                invalid.append(f"message:{index}:not_mapping")
                continue
            role = str(message.get("role", ""))
            if role == "assistant" and message.get("tool_calls"):
                if pending:
                    invalid.append(f"message:{index}:unresolved_tool_calls")
                for call in message.get("tool_calls") or ():
                    call_id = str(call.get("id", "")) if isinstance(call, Mapping) else ""
                    if not call_id or call_id in pending:
                        invalid.append(f"message:{index}:invalid_tool_call_id")
                        continue
                    pending.add(call_id)
                    assistant_calls += 1
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id", ""))
                if not call_id or call_id not in pending:
                    invalid.append(f"message:{index}:unknown_tool_call_id")
                else:
                    pending.remove(call_id)
                    tool_results += 1
                continue
            if pending and role in {"user", "assistant", "system"}:
                invalid.append(f"message:{index}:tool_result_sequence_interrupted")
        if pending:
            invalid.append("pending_tool_calls_unanswered")
        user_present = any(
            isinstance(message, Mapping) and str(message.get("role", "")) == "user"
            and str(message.get("content", "") or "").strip()
            for message in messages
        )
        if requirements.get("native_seed_required", True) and not user_present:
            invalid.append("native_user_seed_missing")
        return not invalid, {
            "message_count": len(messages),
            "assistant_tool_call_count": assistant_calls,
            "tool_result_count": tool_results,
            "user_seed_present": user_present,
            "invalid_reasons": invalid,
        }

    @staticmethod
    def _check_legacy(protocol, prompt, model_input):
        if protocol not in {"legacy_text", "legacy_stream"}:
            return None, {"required": False}
        return bool(isinstance(prompt, str) and prompt.strip()), {
            "prompt_chars": len(prompt),
            "streaming": protocol == "legacy_stream",
        }

    @staticmethod
    def _fallback_evidence(metadata, requirements):
        if requirements.get("fallback_used") is True or metadata.get("fallback_used") is True:
            return True, str(
                requirements.get("fallback_reason")
                or metadata.get("fallback_reason")
                or "DECLARED_FALLBACK"
            )
        budget_source = str(metadata.get("budget_source", "") or "")
        if budget_source == "fallback":
            return True, "BUDGET_FALLBACK"
        if int(metadata.get("compression_failure_count", 0) or 0) > 0:
            return True, "COMPRESSION_FALLBACK"
        return False, ""


__all__ = [
    "ALLOW",
    "ContextValidationError",
    "ContextValidationEvidence",
    "ContextValidationResult",
    "ContextValidator",
    "HARD",
    "INVALID",
    "REJECT",
    "RETRY_ASSEMBLY",
    "SOFT",
    "VALID",
    "VALID_WITH_FALLBACK",
    "ValidationCheck",
]
