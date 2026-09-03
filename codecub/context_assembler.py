"""The single provider-facing context assembly boundary.

``ContextCompiler`` owns the existing context processing algorithms: budget
calculation, history condensation, freshness and range-aware evidence.  This
module owns the last mile only: selecting the protocol, supplying the
already-produced sources to the compiler, and returning the provider-bound
prompt or structured messages.

The assembler intentionally accepts value-like source data and narrow
collaborators.  It never receives a ``Pico``/Runtime object and it does not
perform retrieval, memory extraction, model invocation, or loop decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .context_compiler import ContextItem

NATIVE_TOOLS_SYSTEM_PROMPT = (
    "You are CodeCub, a local coding agent. Tools are provided "
    "through native tool calling. When a tool is required, use the "
    "provided native tool-call mechanism. Do not emit XML tool tags "
    "or write tool calls as plain text."
)
NATIVE_TOOLS_USER_PREFIX = (
    "For file tools, use workspace-relative paths; "
    "shell commands already execute at the workspace root."
)


@dataclass(frozen=True)
class ContextSources:
    """All materialized inputs needed for one provider-bound assembly.

    Retrieval and Memory v2 remain producers/processors outside this type:
    ``memory_layer`` is the bounded rendered result already returned by the
    existing memory pipeline, while ``pinned_extra`` carries existing pinned
    runtime/workspace constraints.  ``native_messages`` is an ordered
    protocol history and is never coerced into one large prompt string.
    """

    user_message: str
    protocol: str = "legacy_text"
    protected_constraints: tuple[str, ...] = ()
    working_state: object | None = None
    history: tuple = ()
    native_messages: tuple = ()
    pinned_extra: Mapping = field(default_factory=dict)
    memory_layer: str = ""
    memory_meta: Mapping = field(default_factory=dict)
    # This is deliberately a resolver output, not raw instruction input.
    # ``instructions`` is a source-compatible alias for integrations that
    # used the shorter name while the Phase 7C boundary was introduced.
    resolved_instructions: object | None = None
    instructions: object | None = None
    # Narrow, non-content requirements consumed by ContextValidator.  The
    # actual source values remain above; this mapping only declares what the
    # current request requires and what freshness evidence was materialized.
    validation_requirements: Mapping = field(default_factory=dict)


@dataclass
class AssembledContext:
    """Provider-bound output plus the compiler metadata used to audit it."""

    protocol: str
    prompt: str = ""
    messages: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    compiled_context: object | None = None
    segments: list = field(default_factory=list)
    validation_requirements: dict = field(default_factory=dict, repr=False)
    resolved_instructions: object | None = field(default=None, repr=False)

    @property
    def model_input(self):
        """Return the exact payload expected by the selected model protocol."""

        return self.messages if self.protocol == "native_tools" else self.prompt

    @property
    def value(self):
        """Compatibility alias for callers that treat output as one value."""

        return self.model_input


class ContextAssembler:
    """Own the final context assembly without depending on the Runtime."""

    def __init__(self, context_compiler=None, legacy_context_manager=None):
        self.context_compiler = context_compiler
        self.legacy_context_manager = legacy_context_manager
        self.last_assembled_context: AssembledContext | None = None
        self.assemble_call_count = 0
        self._last_native_preamble = None

    def bind_legacy_context_manager(self, legacy_context_manager):
        """Attach the deprecated feature-flag-off builder after composition."""

        self.legacy_context_manager = legacy_context_manager
        return self

    @staticmethod
    def _segments_from(compiled_context):
        if compiled_context is None:
            return []
        segments = []
        for field_name in (
            "pinned",
            "recent_items",
            "raw_evidence_items",
            "compressed_history_items",
            "repo_map_items",
        ):
            segments.extend(list(getattr(compiled_context, field_name, []) or []))
        return segments

    @staticmethod
    def render_user_message(user_message, protected_constraints=()):
        """Render the user input with protected constraints exactly once."""

        message = str(user_message)
        constraints = [str(item).strip() for item in protected_constraints if str(item).strip()]
        if not constraints:
            return message
        rendered = "\n".join(f"- {item}" for item in constraints)
        suffix = f"\n\nProtected runtime constraints (must obey):\n{rendered}"
        return message if suffix in message else message + suffix

    @staticmethod
    def _resolved_instructions(sources):
        return sources.resolved_instructions or sources.instructions

    @classmethod
    def _instruction_items(cls, resolved):
        if resolved is None:
            return ()
        return tuple(getattr(resolved, "instructions", ()) or ())

    @staticmethod
    def _layer_value(item):
        layer = getattr(item, "layer", "")
        return str(getattr(layer, "value", layer))

    @classmethod
    def _is_task_instruction(cls, item):
        return cls._layer_value(item) == "task"

    @staticmethod
    def _instruction_key(item):
        prefix = "instruction:legacy:" if bool(
            (getattr(item, "metadata", {}) or {}).get("legacy_prefix")
        ) else "instruction:"
        return prefix + str(item.id)

    @classmethod
    def _instruction_extras(cls, resolved):
        """Return one pinned value per resolved instruction.

        The values contain model-visible content only.  Provenance is attached
        to the corresponding ``ContextItem`` after the compiler has produced
        the final provider-bound context.
        """

        extras = {}
        protected = []
        ordered_items = []
        for item in cls._instruction_items(resolved):
            # The compiler already owns the canonical user-task segment.  Do
            # not render it a second time; it is still represented as an
            # instruction audit segment below.
            if cls._is_task_instruction(item):
                continue
            if bool(getattr(item, "protected", False)) or cls._layer_value(item) == "protected_runtime":
                protected.append(str(item.content).strip())
                ordered_items.append(("protected", item))
                continue
            key = cls._instruction_key(item)
            content = str(item.content or "").strip()
            if content:
                extras[key] = content
                ordered_items.append((key, item))
        if protected:
            protected_text = (
                "Protected runtime constraints (must obey):\n"
                + "\n".join(f"- {item}" for item in protected)
            )
            ordered = {}
            added_protected = False
            for key, _item in ordered_items:
                if key == "protected":
                    if not added_protected:
                        ordered["pinned:runtime-constraints"] = protected_text
                        added_protected = True
                else:
                    ordered[key] = extras[key]
            return ordered
        return extras

    @classmethod
    def _instruction_rendered_text(cls, resolved):
        items = tuple(
            item
            for item in cls._instruction_items(resolved)
            if not cls._is_task_instruction(item)
        )
        protected = [
            item
            for item in items
            if bool(getattr(item, "protected", False))
            or cls._layer_value(item) == "protected_runtime"
        ]
        ordinary = [item for item in items if item not in protected]
        parts = []
        if ordinary:
            parts.append(
                "Instructions:\n"
                + "\n".join(
                    f"- {str(item.content).strip()}"
                    for item in ordinary
                    if str(item.content).strip()
                )
            )
        if protected:
            parts.append(
                "Protected runtime constraints (must obey):\n"
                + "\n".join(f"- {str(item.content).strip()}" for item in protected)
            )
        if not parts:
            return ""
        return "\n\n".join(parts)

    @classmethod
    def _resolved_protected(cls, resolved):
        return tuple(
            str(item.content).strip()
            for item in cls._instruction_items(resolved)
            if bool(getattr(item, "protected", False))
            or cls._layer_value(item) == "protected_runtime"
            if str(item.content).strip()
        )

    @classmethod
    def _augment_pinned_extra(cls, sources, resolved):
        extras = cls._instruction_extras(resolved)
        extras.update(dict(sources.pinned_extra or {}))
        return extras

    @classmethod
    def _attach_instruction_segments(cls, result, resolved):
        items = cls._instruction_items(resolved)
        if not items:
            return
        by_key = {
            str(getattr(segment, "key", "")): segment
            for segment in result.segments
        }
        for item in items:
            key = cls._instruction_key(item)
            provenance = {
                "instruction_id": item.id,
                "source": item.source,
                "layer": item.layer.value,
                "scope": item.scope.value,
                "scope_id": item.scope_id,
                "protected": bool(item.protected),
            }
            for field_name in (
                "source_kind",
                "source_path",
                "scope_path",
                "repository_root",
                "specificity_depth",
                "template_filename",
                "file_freshness",
            ):
                if field_name in (item.metadata or {}):
                    provenance[field_name] = item.metadata[field_name]
            segment = by_key.get(key)
            if segment is None:
                result.segments.append(
                    ContextItem(key=key, kind="instruction", text=item.content, provenance=provenance)
                )
            else:
                segment.kind = "instruction"
                segment.provenance = {**dict(segment.provenance or {}), **provenance}

    @classmethod
    def _instruction_requirements(cls, sources, resolved, requirements):
        if resolved is None:
            return requirements
        items = cls._instruction_items(resolved)
        requirements["resolved_instructions"] = resolved
        requirements["instruction_ids"] = tuple(item.id for item in items)
        integrity_items = tuple(
            item
            for item in items
            if not bool((getattr(item, "metadata", {}) or {}).get("legacy_prefix"))
        )
        requirements["instruction_integrity_ids"] = tuple(
            item.id for item in integrity_items
        )
        requirements["instruction_segment_keys"] = tuple(
            cls._instruction_key(item)
            for item in integrity_items
            if not cls._is_task_instruction(item)
        )
        requirements["required_protected_instruction_ids"] = tuple(
            item.id
            for item in items
            if bool(getattr(item, "protected", False))
            or str(getattr(getattr(item, "layer", None), "value", getattr(item, "layer", "")))
            == "protected_runtime"
        )
        protected = cls._resolved_protected(resolved)
        if protected:
            requirements["protected_constraints"] = protected
            requirements["protected_segment_keys"] = ("pinned:runtime-constraints",)
        return requirements

    @staticmethod
    def _native_pinned_extra(sources, resolved=None):
        extras = ContextAssembler._augment_pinned_extra(sources, resolved)
        resolved_protected = ContextAssembler._resolved_protected(resolved)
        constraints = [
            str(item).strip()
            for item in sources.protected_constraints
            if str(item).strip()
        ]
        if constraints and not resolved_protected:
            extras.setdefault(
                "pinned:runtime-constraints",
                "Protected runtime constraints (must obey):\n"
                + "\n".join(f"- {item}" for item in constraints),
            )
        return extras

    @classmethod
    def _validation_requirements(cls, sources):
        requirements = dict(sources.validation_requirements or {})
        required = [str(item) for item in requirements.get("required_sources", ()) or ()]
        if "user_task" not in required:
            required.insert(0, "user_task")
        requirements["required_sources"] = tuple(dict.fromkeys(required))
        optional = [str(item) for item in requirements.get("optional_sources", ()) or ()]
        requirements["optional_sources"] = tuple(dict.fromkeys(optional))
        resolved = cls._resolved_instructions(sources)
        requirements = cls._instruction_requirements(sources, resolved, requirements)
        protected = cls._resolved_protected(resolved)
        if sources.protected_constraints and not protected:
            requirements["protected_constraints"] = tuple(
                sources.protected_constraints
            )
            requirements.setdefault(
                "protected_segment_keys", ("pinned:runtime-constraints",)
            )
        return requirements

    def initial_native_messages(self, user_message):
        """Create the ordered native protocol seed for a turn."""

        return [
            {"role": "system", "content": NATIVE_TOOLS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{NATIVE_TOOLS_USER_PREFIX}\n\n{str(user_message)}",
            },
        ]

    def assemble(
        self,
        sources=None,
        *,
        protocol=None,
        user_message=None,
        protected_constraints=(),
        working_state=None,
        history=None,
        native_messages=None,
        pinned_extra=None,
        memory_layer=None,
        memory_meta=None,
        resolved_instructions=None,
        instructions=None,
        validation_requirements=None,
    ):
        """Assemble one legacy prompt or one native structured message list.

        ``ContextSources`` is the preferred API.  Explicit keyword inputs are
        supported as a small compatibility convenience for focused tests and
        extensions; both paths produce the same deterministic source object.
        """

        if sources is None:
            if user_message is None:
                raise ValueError("ContextAssembler.assemble requires user_message")
            sources = ContextSources(
                user_message=str(user_message),
                protocol=str(protocol or "legacy_text"),
                protected_constraints=tuple(protected_constraints or ()),
                working_state=working_state,
                history=tuple(history or ()),
                native_messages=tuple(native_messages or ()),
                pinned_extra=dict(pinned_extra or {}),
                memory_layer=str(memory_layer or ""),
                memory_meta=dict(memory_meta or {}),
                resolved_instructions=resolved_instructions,
                instructions=instructions,
                validation_requirements=dict(validation_requirements or {}),
            )
        elif isinstance(sources, Mapping):
            sources = ContextSources(**dict(sources))
        elif not isinstance(sources, ContextSources):
            raise TypeError("sources must be ContextSources or a mapping")

        selected_protocol = str(protocol or sources.protocol or "legacy_text")
        resolved_instructions = self._resolved_instructions(sources)
        resolved_protected = self._resolved_protected(resolved_instructions)
        effective_message = self.render_user_message(
            sources.user_message,
            () if resolved_protected else sources.protected_constraints,
        )
        validation_requirements = self._validation_requirements(sources)
        compiler = self.context_compiler
        metadata = {}
        compiled_context = None
        raw_tokens = None

        if selected_protocol == "native_tools":
            original_native_messages = (
                sources.native_messages
                if isinstance(sources.native_messages, list)
                else None
            )
            native_history = list(sources.native_messages)
            if self._last_native_preamble is not None:
                native_history = [
                    message
                    for message in native_history
                    if message is not self._last_native_preamble
                ]
            if not native_history:
                native_history = self.initial_native_messages(sources.user_message)
            if compiler is None:
                preamble = [
                    str(value).strip()
                    for value in self._native_pinned_extra(sources, resolved_instructions).values()
                    if str(value).strip()
                ]
                if sources.working_state is not None and hasattr(
                    sources.working_state, "to_text"
                ):
                    state_text = str(sources.working_state.to_text()).strip()
                    if state_text:
                        preamble.append(state_text)
                if str(sources.memory_layer or "").strip():
                    preamble.append(str(sources.memory_layer).strip())
                messages = native_history
                if preamble:
                    messages = [
                        {"role": "system", "content": "\n\n".join(preamble)},
                        *native_history,
                    ]
                result_metadata = {
                    "compiled_context_tokens": None,
                    "memory_layer_rendered": bool(str(sources.memory_layer or "").strip()),
                    "memory_tokens": len(str(sources.memory_layer or "")),
                }
            else:
                estimate_native_tokens = getattr(compiler, "_estimate_native_tokens", None)
                if callable(estimate_native_tokens):
                    raw_tokens = estimate_native_tokens(native_history)
                compiler_kwargs = {
                    "working_state": sources.working_state,
                    "native_messages": native_history,
                    "pinned_extra": self._native_pinned_extra(sources, resolved_instructions),
                    "memory_layer": sources.memory_layer,
                    "memory_meta": dict(sources.memory_meta or {}),
                    "include_context": True,
                }
                try:
                    messages, metadata = compiler.compile_native(
                        effective_message, **compiler_kwargs
                    )
                except TypeError as exc:
                    # Keep the assembler usable with narrow third-party/test
                    # compilers that predate the optional include_context port.
                    if "include_context" not in str(exc):
                        raise
                    compiler_kwargs.pop("include_context")
                    messages, metadata = compiler.compile_native(
                        effective_message, **compiler_kwargs
                    )
                compiled_context = getattr(compiler, "last_compiled_context", None)
            if original_native_messages is not None:
                original_native_messages[:] = messages
                messages = original_native_messages
            original_seed = (
                native_history[0]
                if native_history and str(native_history[0].get("role", "")) == "system"
                else None
            )
            self._last_native_preamble = None
            if messages:
                if original_seed is not None and messages[0] is original_seed:
                    if len(messages) > 1 and messages[1].get("role") == "system":
                        self._last_native_preamble = messages[1]
                elif messages[0].get("role") == "system":
                    self._last_native_preamble = messages[0]
            result = AssembledContext(
                protocol=selected_protocol,
                messages=messages,
                metadata=dict(metadata or result_metadata if compiler is None else metadata or {}),
                compiled_context=compiled_context,
                segments=self._segments_from(compiled_context),
                validation_requirements=dict(validation_requirements),
                resolved_instructions=resolved_instructions,
            )
        elif compiler is not None:
            # Keep the pre-Phase-7A narrow compiler compatibility contract:
            # third-party compilers that do not expose compiled segments still
            # receive the rendered constraint suffix as their user input.  The
            # production ContextCompiler has structured pinned segments, so it
            # receives the raw user input plus a dedicated constraint segment.
            compiler_user_message = (
                str(sources.user_message)
                if hasattr(compiler, "last_compiled_context")
                else effective_message
            )
            if not hasattr(compiler, "last_compiled_context"):
                instruction_text = self._instruction_rendered_text(resolved_instructions)
                if instruction_text:
                    compiler_user_message = f"{instruction_text}\n\n{compiler_user_message}"
            prompt, metadata = compiler.compile_text(
                compiler_user_message,
                working_state=sources.working_state,
                history=list(sources.history),
                pinned_extra=self._native_pinned_extra(sources, resolved_instructions),
                memory_layer=sources.memory_layer,
                memory_meta=dict(sources.memory_meta or {}),
            )
            result = AssembledContext(
                protocol=selected_protocol,
                prompt=str(prompt),
                metadata=dict(metadata or {}),
                compiled_context=getattr(compiler, "last_compiled_context", None),
                validation_requirements=dict(validation_requirements),
                resolved_instructions=resolved_instructions,
            )
            result.segments = self._segments_from(result.compiled_context)
            raw_tokens = result.metadata.get("candidate_context_tokens")
        elif self.legacy_context_manager is not None:
            prompt, metadata = self.legacy_context_manager.build(effective_message)
            instruction_text = self._instruction_rendered_text(resolved_instructions)
            if instruction_text:
                prompt = f"{instruction_text}\n\n{prompt}"
            result = AssembledContext(
                protocol=selected_protocol,
                prompt=str(prompt),
                metadata=dict(metadata or {}),
                validation_requirements=dict(validation_requirements),
                resolved_instructions=resolved_instructions,
            )
        else:
            # This is only a narrow fallback for isolated callers.  Production
            # composition always supplies either the compiler or legacy builder.
            result = AssembledContext(
                protocol=selected_protocol,
                prompt=(
                    f"{self._instruction_rendered_text(resolved_instructions)}\n\n{effective_message}"
                    if self._instruction_rendered_text(resolved_instructions)
                    else effective_message
                ),
                metadata={},
                validation_requirements=dict(validation_requirements),
                resolved_instructions=resolved_instructions,
            )

        self._attach_instruction_segments(result, resolved_instructions)
        if resolved_instructions is not None:
            result.metadata.update(
                {
                    "instruction_count": len(self._instruction_items(resolved_instructions)),
                    "instruction_deduplicated_count": int(
                        getattr(resolved_instructions, "deduplicated_count", 0)
                    ),
                    "instruction_protected_count": int(
                        getattr(resolved_instructions, "protected_count", 0)
                    ),
                    "instruction_shadowed_count": int(
                        getattr(resolved_instructions, "shadowed_count", 0)
                    ),
                    "instruction_conflict_count": int(
                        getattr(resolved_instructions, "conflict_count", 0)
                    ),
                    "instruction_ids": [item.id for item in self._instruction_items(resolved_instructions)],
                    "instruction_shadowed_ids": [
                        item.id for item in getattr(resolved_instructions, "shadowed", ())
                    ],
                    "instruction_duplicate_count": int(
                        getattr(resolved_instructions, "deduplicated_count", 0)
                    ),
                    "unexpected_instruction_duplicates": 0,
                    "instruction_conflicts": [
                        item.to_dict() for item in getattr(resolved_instructions, "conflicts", ())
                    ],
                }
            )

        self.assemble_call_count += 1
        if raw_tokens is not None:
            result.metadata.setdefault("assembler_raw_tokens", raw_tokens)
        if result.metadata.get("compiled_context_tokens") is not None:
            result.metadata.setdefault(
                "assembler_final_tokens",
                result.metadata.get("compiled_context_tokens"),
            )
        result.metadata.setdefault("context_assembler", "context_assembler")
        result.metadata.setdefault("assembly_protocol", selected_protocol)
        result.metadata.setdefault(
            "validation_input",
            {
                "required_sources": list(
                    validation_requirements.get("required_sources", ())
                ),
                "optional_sources": list(
                    validation_requirements.get("optional_sources", ())
                ),
                "protected_segment_keys": list(
                    validation_requirements.get("protected_segment_keys", ())
                ),
                "freshness_required": bool(
                    validation_requirements.get("freshness_required", False)
                ),
            },
        )
        result.metadata.setdefault(
            "assembly_source_order",
            [
                "resolved_instructions",
                "pinned",
                "working_state",
                "memory_layer",
                "compressed_history",
                "raw_evidence",
                "repo_map",
                "native_or_legacy_history",
            ],
        )
        self.last_assembled_context = result
        return result
