"""Explicit workspace-boundary execution contract.

This is a path and workspace boundary, not an OS/container sandbox.  Shell
commands continue to run as host processes under the existing approval and
environment policy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxDescriptor:
    mode: str = "workspace_boundary"
    path_containment: bool = True
    symlink_escape_prevented: bool = True
    host_process_isolation: bool = False
    network_isolation: bool = False
    filesystem_isolation: bool = False

    def to_dict(self) -> dict[str, bool | str]:
        return {
            "mode": self.mode,
            "path_containment": self.path_containment,
            "symlink_escape_prevented": self.symlink_escape_prevented,
            "host_process_isolation": self.host_process_isolation,
            "network_isolation": self.network_isolation,
            "filesystem_isolation": self.filesystem_isolation,
        }

class WorkspaceBoundarySandbox:
    """Resolve workspace paths and state what is and is not isolated."""

    descriptor = SandboxDescriptor()

    def __init__(self, root):
        self.root = Path(root).resolve()

    def resolve_path(self, raw_path) -> Path:
        path = Path(raw_path)
        candidate = path if path.is_absolute() else self.root / path
        resolved = candidate.resolve()
        try:
            inside = os.path.commonpath((str(self.root), str(resolved))) == str(self.root)
        except ValueError:
            inside = False
        if not inside:
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved

    def describe(self) -> dict:
        return {
            **self.descriptor.to_dict(),
            "root": str(self.root),
            "execution_note": "host process; approval and environment policy still apply",
        }
