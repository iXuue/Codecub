"""Manifest-only plugin and skill discovery with explicit lazy activation."""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Mapping


class ExtensionError(ValueError):
    """Invalid extension manifest or activation request."""


class ExtensionConflict(ExtensionError):
    """Two manifests claim the same extension name without clear priority."""


@dataclass(frozen=True)
class ExtensionContext:
    """Explicit capability-scoped context supplied during activation."""

    runtime: Any = None
    tool_registry: Any = None
    event_bus: Any = None
    granted_capabilities: frozenset[str] = frozenset()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def require(self, capability: str) -> None:
        if str(capability).strip() not in self.granted_capabilities:
            raise ExtensionError(f"extension capability denied: {capability}")


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MANIFEST_NAMES = {"manifest.json", "plugin.json", "skill.json"}
_MAX_MANIFEST_BYTES = 256 * 1024


@dataclass(frozen=True)
class ExtensionManifest:
    name: str
    kind: str
    version: str = "0"
    description: str = ""
    entrypoint: str = ""
    source: str = ""
    priority: int = 0
    capabilities: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: str = "") -> "ExtensionManifest":
        if not isinstance(raw, dict):
            raise ExtensionError("extension manifest must be an object")
        name = str(raw.get("name") or raw.get("id") or "").strip()
        if not _NAME_PATTERN.fullmatch(name):
            raise ExtensionError("extension name is invalid")
        kind = str(raw.get("kind") or "plugin").strip().lower()
        if kind not in {"plugin", "skill"}:
            raise ExtensionError("extension kind must be plugin or skill")
        entrypoint = str(raw.get("entrypoint") or "").strip()
        if entrypoint and not _valid_entrypoint(entrypoint):
            raise ExtensionError("extension entrypoint must be module:attribute")
        capabilities = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in raw.get("capabilities", ()) or ()
                    if str(item).strip()
                }
            )
        )
        dependencies = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in raw.get("dependencies", ()) or ()
                    if str(item).strip()
                }
            )
        )
        try:
            priority = int(raw.get("priority", 0))
        except (TypeError, ValueError) as exc:
            raise ExtensionError("extension priority must be an integer") from exc
        return cls(
            name=name,
            kind=kind,
            version=str(raw.get("version") or "0").strip(),
            description=str(raw.get("description") or "").strip(),
            entrypoint=entrypoint,
            source=str(source or ""),
            priority=priority,
            capabilities=capabilities,
            dependencies=dependencies,
            metadata=dict(raw.get("metadata") or {})
            if isinstance(raw.get("metadata"), dict)
            else {},
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "ExtensionManifest":
        manifest_path = Path(path)
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ExtensionError("extension manifest is too large")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExtensionError(f"cannot read extension manifest: {manifest_path}") from exc
        return cls.from_dict(raw, source=str(manifest_path.resolve()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "source": self.source,
            "priority": self.priority,
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
        }


def _valid_entrypoint(value: str) -> bool:
    module, separator, attribute = value.partition(":")
    if not separator or not module or not attribute:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module)) and bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", attribute)
    )


@dataclass
class _ExtensionEntry:
    manifest: ExtensionManifest
    factory: Callable | None = None
    active: Any = None
    error: str = ""


class ExtensionRegistry:
    """Discover manifests without executing them; activate only by explicit call."""

    def __init__(self):
        self._entries: dict[str, _ExtensionEntry] = {}
        self.errors: list[dict[str, str]] = []
        self._lock = RLock()

    def register(
        self,
        manifest: ExtensionManifest,
        factory: Callable | None = None,
        *,
        replace: bool = False,
    ):
        name = manifest.name.casefold()
        existing = self._entries.get(name)
        if existing is not None and not replace:
            if manifest.priority <= existing.manifest.priority:
                raise ExtensionConflict(
                    f"extension conflict for {manifest.name}: "
                    f"{existing.manifest.source} vs {manifest.source}"
                )
            replace = True
        if existing is not None and replace and manifest.priority < existing.manifest.priority:
            raise ExtensionConflict(
                f"lower priority extension cannot replace {manifest.name}"
            )
        self._entries[name] = _ExtensionEntry(manifest, factory)
        return manifest

    def discover(self, roots: Iterable[str | Path]):
        """Read known manifest filenames; no import or arbitrary code execution."""

        for root_value in roots:
            root = Path(root_value)
            if not root.exists():
                continue
            paths = [root] if root.is_file() else sorted(root.rglob("*.json"))
            for path in paths:
                if path.name.casefold() not in _MANIFEST_NAMES:
                    continue
                try:
                    self.register(ExtensionManifest.from_file(path))
                except ExtensionError as exc:
                    self.errors.append({"source": str(path), "error": str(exc)})
        return self

    def register_factory(self, name: str, factory: Callable):
        entry = self._entries.get(str(name).casefold())
        if entry is None:
            raise KeyError(name)
        entry.factory = factory
        entry.active = None
        return entry.manifest

    def activate(
        self,
        name: str,
        loader: Callable[[str], Callable] | None = None,
        *,
        context: ExtensionContext | None = None,
        granted_capabilities: Iterable[str] = (),
        _stack: tuple[str, ...] = (),
    ):
        entry = self._entries.get(str(name).casefold())
        if entry is None:
            raise KeyError(name)
        if entry.active is not None:
            return entry.active
        normalized = entry.manifest.name.casefold()
        if normalized in _stack:
            raise ExtensionError(f"extension dependency cycle: {' -> '.join((*_stack, normalized))}")
        requested = set(entry.manifest.capabilities)
        if context is not None:
            granted = set(context.granted_capabilities)
        else:
            granted = {str(item).strip() for item in granted_capabilities if str(item).strip()}
        if requested - granted:
            missing = ", ".join(sorted(requested - granted))
            raise ExtensionError(f"extension capabilities require explicit grant: {missing}")
        for dependency in entry.manifest.dependencies:
            self.activate(
                dependency,
                loader=loader,
                context=context,
                granted_capabilities=granted,
                _stack=(*_stack, normalized),
            )
        try:
            if entry.manifest.kind == "skill" and not entry.manifest.entrypoint:
                active = entry.manifest
            else:
                factory = entry.factory or self._load_factory(entry.manifest.entrypoint, loader)
                if not callable(factory):
                    raise ExtensionError(f"extension factory is not callable: {name}")
                entry.factory = factory
                active = factory(entry.manifest)
            if context is not None:
                activate_hook = getattr(active, "activate", None)
                if callable(activate_hook):
                    activate_hook(context)
                register_tools = getattr(active, "register_tools", None)
                if callable(register_tools):
                    register_tools(context.tool_registry)
            entry.active = active
            entry.error = ""
            return active
        except Exception as exc:
            entry.error = str(exc)
            raise ExtensionError(f"extension activation failed: {name}: {exc}") from exc

    def activate_all(
        self,
        names: Iterable[str] | None = None,
        *,
        context: ExtensionContext | None = None,
        granted_capabilities: Iterable[str] = (),
        loader: Callable[[str], Callable] | None = None,
    ) -> dict[str, Any]:
        selected = tuple(names) if names is not None else tuple(
            manifest.name for manifest in self.manifests()
        )
        activated = {}
        for name in selected:
            activated[str(name)] = self.activate(
                name,
                loader=loader,
                context=context,
                granted_capabilities=granted_capabilities,
            )
        return activated

    def deactivate(self, name: str, context: ExtensionContext | None = None) -> bool:
        entry = self._entries.get(str(name).casefold())
        if entry is None or entry.active is None:
            return False
        active, entry.active = entry.active, None
        hook = getattr(active, "deactivate", None) or getattr(active, "close", None)
        if callable(hook):
            try:
                hook(context)
            except TypeError:
                hook()
        return True

    def deactivate_all(self, context: ExtensionContext | None = None) -> None:
        for manifest in reversed(self.manifests()):
            self.deactivate(manifest.name, context)

    @staticmethod
    def _load_factory(entrypoint: str, loader=None):
        if not _valid_entrypoint(entrypoint):
            raise ExtensionError("extension entrypoint is required for activation")
        if loader is not None:
            return loader(entrypoint)
        module_name, _, attribute = entrypoint.partition(":")
        module = importlib.import_module(module_name)
        return getattr(module, attribute)

    def get(self, name: str) -> ExtensionManifest | None:
        entry = self._entries.get(str(name).casefold())
        return entry.manifest if entry else None

    def manifests(self, kind: str | None = None) -> tuple[ExtensionManifest, ...]:
        entries = self._entries.values()
        if kind:
            entries = [entry for entry in entries if entry.manifest.kind == kind]
        return tuple(entry.manifest for entry in entries)

    def to_dict(self) -> dict:
        return {
            "manifests": [manifest.to_dict() for manifest in self.manifests()],
            "errors": list(self.errors),
            "active": sorted(
                name for name, entry in self._entries.items() if entry.active is not None
            ),
            "activation_errors": {
                name: entry.error
                for name, entry in self._entries.items()
                if entry.error
            },
        }
