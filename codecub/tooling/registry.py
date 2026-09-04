"""Mutable, mapping-compatible tool registry for the CodeCub runtime.

The registry is intentionally an adapter around CodeCub's existing dictionary
tool specs.  Existing callers can still use ``items()``, ``get()``, indexing,
and direct spec mutation, while new callers get explicit register/unregister,
capability lookup, and filtered registries for future MCP/plugin sources.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any, Mapping

from .contracts import ToolCapability


class ToolRegistry(MutableMapping[str, dict[str, Any]]):
    """A live name-to-tool-spec catalog with legacy mapping compatibility."""

    def __init__(self, tools: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        for name, tool in (tools or {}).items():
            self.register(name, tool)

    def __getitem__(self, name: str) -> dict[str, Any]:
        return self._tools[name]

    def __setitem__(self, name: str, tool: Mapping[str, Any]) -> None:
        self.register(name, tool, replace=True)

    def __delitem__(self, name: str) -> None:
        del self._tools[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def register(
        self,
        name: str,
        tool: Mapping[str, Any],
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Register one spec and fail closed on accidental name collisions."""

        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("tool name must not be empty")
        if normalized in self._tools and not replace:
            raise ValueError(f"tool '{normalized}' is already registered")
        if not isinstance(tool, Mapping):
            raise TypeError("tool spec must be a mapping")
        spec = dict(tool)
        spec["capability"] = self._coerce_capability(spec, normalized)
        self._tools[normalized] = spec
        return spec

    def unregister(self, name: str) -> None:
        """Remove a tool idempotently, matching Pico's registry seam."""

        self._tools.pop(str(name or "").strip(), None)

    def resolve(self, name: str) -> dict[str, Any] | None:
        return self._tools.get(str(name or "").strip())

    def has(self, name: str) -> bool:
        return self.resolve(name) is not None

    def get_definitions(self) -> list[dict[str, Any]]:
        """Return current provider-facing definitions without caching them."""

        # Import lazily so the legacy ``codecub.tools`` facade can import this
        # registry while the package is being initialized.
        from ..tools import native_tool_definitions

        return native_tool_definitions(self)

    def capability(self, name: str) -> ToolCapability:
        tool = self.resolve(name)
        return self._coerce_capability(tool or {}, str(name or "").strip())

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def filtered(self, names: set[str] | tuple[str, ...] | list[str]) -> "ToolRegistry":
        allowed = {str(name) for name in names}
        return ToolRegistry({name: tool for name, tool in self.items() if name in allowed})

    @staticmethod
    def _coerce_capability(
        tool: Mapping[str, Any],
        name: str,
    ) -> ToolCapability:
        raw = tool.get("capability")
        if isinstance(raw, ToolCapability):
            return raw
        if isinstance(raw, Mapping):
            try:
                effect = raw.get("effect", "unknown")
                from .contracts import ToolEffect

                effect = (
                    effect
                    if isinstance(effect, ToolEffect)
                    else ToolEffect(str(effect).strip().lower())
                )
            except ValueError:
                effect = ToolCapability.from_legacy(tool, name=name).effect
            return ToolCapability(
                effect=effect,
                concurrency_safe=bool(raw.get("concurrency_safe", False)),
                idempotent=bool(raw.get("idempotent", False)),
                retryable=bool(raw.get("retryable", False)),
            )
        return ToolCapability.from_legacy(tool, name=name)
