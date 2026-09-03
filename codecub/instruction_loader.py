"""Deterministic loading of repository instruction files.

The loader is deliberately narrower than :class:`InstructionResolver`:
it discovers and reads files, records bounded failure evidence, and attaches
repository provenance.  It never decides cross-layer precedence and never
receives a Runtime/Pico object.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .instructions import Instruction, InstructionLayer, InstructionScope
from .memory import resolve_workspace_path


DEFAULT_INSTRUCTION_FILENAMES = ("AGENTS.md",)
DEFAULT_MAX_INSTRUCTION_FILE_BYTES = 256 * 1024


@dataclass(frozen=True)
class InstructionLoadResult:
    """One bounded discovery pass and its audit evidence."""

    instructions: tuple[Instruction, ...] = ()
    discovered_files: tuple[str, ...] = ()
    loaded_files: tuple[str, ...] = ()
    ignored_files: tuple[dict, ...] = ()
    load_errors: tuple[dict, ...] = ()
    target_paths: tuple[str, ...] = ()

    @property
    def instruction_count(self):
        return len(self.instructions)

    @property
    def loaded_count(self):
        return len(self.loaded_files)

    @property
    def error_count(self):
        return len(self.load_errors)

    def to_dict(self):
        return {
            "instruction_count": self.instruction_count,
            "discovered_files": list(self.discovered_files),
            "loaded_files": list(self.loaded_files),
            "ignored_files": [dict(item) for item in self.ignored_files],
            "load_errors": [dict(item) for item in self.load_errors],
            "target_paths": list(self.target_paths),
        }


class InstructionLoader:
    """Load repository-scoped instruction-bearing files.

    ``target_paths`` is optional.  With no target path only the repository
    root file is eligible.  When paths are known, the loader walks only their
    ancestor directories, yielding root-to-deepest deterministic order.  A
    fresh pass reads the current file bytes every time; this intentionally
    avoids stale cache semantics during a run.
    """

    def __init__(
        self,
        workspace_root,
        *,
        filenames=DEFAULT_INSTRUCTION_FILENAMES,
        max_file_bytes=DEFAULT_MAX_INSTRUCTION_FILE_BYTES,
    ):
        root = Path(workspace_root).resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        names = tuple(dict.fromkeys(str(name).strip() for name in filenames if str(name).strip()))
        if not names:
            raise ValueError("filenames must contain at least one instruction filename")
        if any(Path(name).name != name or name in {".", ".."} for name in names):
            raise ValueError("instruction filenames must be plain file names")
        limit = int(max_file_bytes)
        if limit < 1:
            raise ValueError("max_file_bytes must be positive")
        self.workspace_root = root
        self.filenames = names
        self.max_file_bytes = limit
        self.load_call_count = 0
        self.last_result = InstructionLoadResult()

    def load(self, target_paths=()):
        """Discover applicable files and return normalized repository instructions."""

        self.load_call_count += 1
        normalized_targets = []
        target_errors = []
        for raw_path in self._as_paths(target_paths):
            resolved = self._safe_resolve(raw_path)
            if resolved is None:
                target_errors.append(
                    {
                        "path": str(raw_path),
                        "code": "workspace_escape",
                        "message": "target path resolves outside workspace",
                    }
                )
                continue
            relative = self._relative(resolved)
            if relative is not None:
                normalized_targets.append(relative)

        candidate_paths = self._candidate_paths(normalized_targets)
        discovered = []
        loaded = []
        ignored = []
        errors = list(target_errors)
        instructions = []
        for candidate in candidate_paths:
            relative = self._relative(candidate)
            if relative is None:
                continue
            safe_candidate = self._safe_resolve(candidate)
            lexical_exists = candidate.exists() or candidate.is_symlink()
            if not lexical_exists:
                continue
            discovered.append(relative)
            if safe_candidate is None:
                errors.append(
                    {
                        "path": relative,
                        "code": "workspace_escape",
                        "message": "instruction file resolves outside workspace",
                    }
                )
                continue
            if not safe_candidate.is_file():
                ignored.append(
                    {"path": relative, "code": "not_a_file", "message": "instruction path is not a file"}
                )
                continue
            try:
                size = safe_candidate.stat().st_size
            except OSError as exc:
                errors.append(self._error(relative, "unreadable", exc))
                continue
            if size > self.max_file_bytes:
                ignored.append(
                    {
                        "path": relative,
                        "code": "oversized",
                        "message": f"instruction file exceeds {self.max_file_bytes} bytes",
                        "size_bytes": size,
                    }
                )
                continue
            try:
                raw = safe_candidate.read_bytes()
                if len(raw) > self.max_file_bytes:
                    ignored.append(
                        {
                            "path": relative,
                            "code": "oversized",
                            "message": f"instruction file exceeds {self.max_file_bytes} bytes",
                            "size_bytes": len(raw),
                        }
                    )
                    continue
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                errors.append(
                    {
                        "path": relative,
                        "code": "invalid_encoding",
                        "message": f"instruction file is not valid UTF-8 at byte {exc.start}",
                    }
                )
                continue
            except OSError as exc:
                errors.append(self._error(relative, "unreadable", exc))
                continue
            content = content.strip()
            if not content:
                ignored.append(
                    {"path": relative, "code": "empty", "message": "instruction file is empty"}
                )
                continue

            scope_path = self._scope_path(candidate.parent)
            metadata = {
                "source_kind": "repository_file",
                "source_path": relative,
                "repository_root": str(self.workspace_root),
                "scope_path": scope_path,
                "specificity_depth": len(Path(scope_path).parts) if scope_path else 0,
                "template_filename": candidate.name,
                "file_freshness": hashlib.sha256(raw).hexdigest(),
                "file_size_bytes": len(raw),
                "required": False,
            }
            instructions.append(
                Instruction(
                    content=content,
                    source="repository",
                    layer=InstructionLayer.REPOSITORY,
                    scope=InstructionScope.REPOSITORY,
                    metadata=metadata,
                )
            )
            loaded.append(relative)

        result = InstructionLoadResult(
            instructions=tuple(instructions),
            discovered_files=tuple(discovered),
            loaded_files=tuple(loaded),
            ignored_files=tuple(ignored),
            load_errors=tuple(errors),
            target_paths=tuple(normalized_targets),
        )
        self.last_result = result
        return result

    @staticmethod
    def _as_paths(value):
        if value is None:
            return ()
        if isinstance(value, (str, os.PathLike)):
            return (value,)
        return tuple(value)

    def _safe_resolve(self, raw_path):
        try:
            return resolve_workspace_path(raw_path, self.workspace_root)
        except (OSError, RuntimeError, ValueError, TypeError):
            return None

    def _relative(self, path):
        try:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.workspace_root / candidate
            # Keep the lexical path here so an escaping symlink can be
            # reported with provenance; _safe_resolve performs the actual
            # resolved workspace-boundary check before reading it.
            return candidate.absolute().relative_to(self.workspace_root).as_posix()
        except (OSError, RuntimeError, ValueError):
            return None

    def _candidate_paths(self, targets):
        directories = {self.workspace_root}
        for relative in targets:
            resolved = self._safe_resolve(relative)
            if resolved is None:
                continue
            current = resolved if resolved.is_dir() else resolved.parent
            while True:
                directories.add(current)
                if current == self.workspace_root:
                    break
                try:
                    current = current.parent
                    current.relative_to(self.workspace_root)
                except ValueError:
                    break
        ordered_directories = sorted(
            directories,
            key=lambda item: (
                len(item.relative_to(self.workspace_root).parts),
                item.relative_to(self.workspace_root).as_posix().casefold(),
            ),
        )
        candidates = []
        seen = set()
        for directory in ordered_directories:
            for filename in self.filenames:
                candidate = directory / filename
                key = os.path.normcase(str(candidate))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
        return candidates

    def _scope_path(self, directory):
        relative = directory.resolve().relative_to(self.workspace_root).as_posix()
        return "" if relative == "." else relative

    @staticmethod
    def _error(path, code, exc):
        return {
            "path": path,
            "code": code,
            "message": f"{exc.__class__.__name__}: {exc}",
        }


__all__ = [
    "DEFAULT_INSTRUCTION_FILENAMES",
    "DEFAULT_MAX_INSTRUCTION_FILE_BYTES",
    "InstructionLoadResult",
    "InstructionLoader",
]
