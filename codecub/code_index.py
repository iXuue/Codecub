"""轻量的 Python AST 符号索引；索引只保存在工作区 .codecub/index。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .workspace import IGNORED_PATH_NAMES


@dataclass(frozen=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    parent: str = ""


class _Visitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.symbols, self.imports, self.calls = [], [], []
        self.parents = []

    def visit_ClassDef(self, node):
        self._add(node, "class")
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node):
        self._function(node, "function", "method")

    def visit_AsyncFunctionDef(self, node):
        self._function(node, "async_function", "async_method")

    def _function(self, node, function_kind, method_kind):
        self._add(node, method_kind if self.parents else function_kind)
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def _add(self, node, kind):
        parent = ".".join(self.parents)
        qualified = ".".join([*self.parents, node.name]) if self.parents else node.name
        self.symbols.append(
            Symbol(
                node.name,
                qualified,
                kind,
                self.path,
                node.lineno,
                getattr(node, "end_lineno", node.lineno),
                parent,
            )
        )

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(
                {
                    "name": alias.asname or alias.name.split(".")[0],
                    "module": alias.name,
                    "line": node.lineno,
                }
            )

    def visit_ImportFrom(self, node):
        module = node.module or ""
        for alias in node.names:
            self.imports.append(
                {
                    "name": alias.asname or alias.name,
                    "module": module,
                    "line": node.lineno,
                }
            )

    def visit_Call(self, node):
        name = _call_name(node.func)
        if name:
            self.calls.append({"name": name, "line": node.lineno})
        self.generic_visit(node)


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


class CodeIndex:
    """持久化、按内容哈希增量刷新的 Python 索引。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.index_dir = self.root / ".codecub" / "index"
        self.path = self.index_dir / "python_symbols.json"
        self.files = {}
        self.last_refresh = {}
        self.load()

    def load(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.files = dict(payload.get("files", {}))
        except (OSError, ValueError, TypeError):
            self.files = {}

    def save(self):
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"version": 1, "files": self.files}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )

    def refresh(self, paths=None):
        candidates = (
            self._python_paths()
            if paths is None
            else [self._resolve_relative(path) for path in paths]
        )
        seen, reindexed, reused, parse_errors = set(), [], [], []
        for path in candidates:
            if path is None:
                continue
            relative = path.relative_to(self.root).as_posix()
            seen.add(relative)
            if not path.exists() or not path.is_file() or path.suffix != ".py":
                if relative in self.files:
                    del self.files[relative]
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if self.files.get(relative, {}).get("content_hash") == digest:
                reused.append(relative)
                continue
            try:
                visitor = _Visitor(relative)
                visitor.visit(ast.parse(text, filename=relative))
                self.files[relative] = {
                    "content_hash": digest,
                    "symbols": [asdict(item) for item in visitor.symbols],
                    "imports": visitor.imports,
                    "calls": visitor.calls,
                    "parse_error": "",
                }
            except SyntaxError as exc:
                self.files[relative] = {
                    "content_hash": digest,
                    "symbols": [],
                    "imports": [],
                    "calls": [],
                    "parse_error": str(exc),
                }
                parse_errors.append(relative)
            reindexed.append(relative)
        if paths is None:
            for relative in list(self.files):
                if relative not in seen:
                    del self.files[relative]
        self.save()
        self.last_refresh = {
            "indexed_files": len(self.files),
            "reindexed_files": reindexed,
            "reused_files": reused,
            "parse_errors": parse_errors,
        }
        return dict(self.last_refresh)

    def symbol_search(self, query, path=".", kind="", limit=20):
        query, prefix, kind = (
            str(query).strip().lower(),
            self._path_prefix(path),
            str(kind).strip().lower(),
        )
        if not query:
            raise ValueError("query must not be empty")
        found = []
        for record in self.files.values():
            for raw in record.get("symbols", []):
                symbol = Symbol(**raw)
                if prefix and not (
                    symbol.path == prefix.rstrip("/") or symbol.path.startswith(prefix)
                ):
                    continue
                if kind and symbol.kind != kind:
                    continue
                if (
                    query in symbol.name.lower()
                    or query in symbol.qualified_name.lower()
                ):
                    found.append(symbol)
        return sorted(
            found,
            key=lambda item: (item.name.lower() != query, item.path, item.start_line),
        )[: max(1, min(int(limit), 100))]

    def file_outline(self, path):
        relative = self._relative_existing(path)
        return [
            Symbol(**raw) for raw in self.files.get(relative, {}).get("symbols", [])
        ]

    def find_references(self, symbol, path="."):
        name, prefix = str(symbol).strip().split(".")[-1], self._path_prefix(path)
        if not name:
            raise ValueError("symbol must not be empty")
        result = []
        for relative, record in self.files.items():
            if prefix and not (
                relative == prefix.rstrip("/") or relative.startswith(prefix)
            ):
                continue
            result.extend(
                (relative, int(call["line"]))
                for call in record.get("calls", [])
                if call.get("name") == name
            )
        return sorted(set(result))

    def _python_paths(self):
        if shutil.which("rg"):
            result = subprocess.run(
                [
                    "rg",
                    "--files",
                    "-g",
                    "*.py",
                    "-g",
                    "!**/.codecub/**",
                    "-g",
                    "!**/.workbuddy/**",
                    "-g",
                    "!**/.uv-cache/**",
                    "-g",
                    "!**/.venv/**",
                    "-g",
                    "!**/venv/**",
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return [self.root / line for line in result.stdout.splitlines() if line]
        paths = []
        for directory, directory_names, file_names in os.walk(self.root):
            directory_names[:] = [
                name
                for name in directory_names
                if name not in IGNORED_PATH_NAMES
                and name not in {".workbuddy", ".uv-cache", ".ruff_cache"}
            ]
            paths.extend(
                Path(directory) / name for name in file_names if name.endswith(".py")
            )
        return paths

    def _resolve_relative(self, value):
        path = (self.root / str(value)).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return None
        return path

    def _relative_existing(self, value):
        path = self._resolve_relative(value)
        if path is None:
            raise ValueError("path escapes workspace")
        return path.relative_to(self.root).as_posix()

    def _path_prefix(self, value):
        relative = self._relative_existing(value)
        return "" if relative == "." else relative.rstrip("/") + "/"
