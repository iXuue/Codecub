"""Portable incremental local vector index for code chunks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .cache import LocalJsonCache, embedding_cache_key


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    symbol: str
    content_hash: str
    text: str


class VectorIndex(Protocol):
    def refresh(self, embedding_client): ...
    def search(self, vector, limit=20): ...


def _cosine(left, right):
    product = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return product / norm if norm else 0.0


class LocalVectorIndex:
    def __init__(self, root, chunks):
        self.root, self.chunks_factory = Path(root), chunks
        self.dir = self.root / ".codecub" / "index"
        self.chunk_path, self.vector_path = (
            self.dir / "semantic_chunks.json",
            self.dir / "semantic_vectors.json",
        )
        self.records, self.vectors = {}, {}
        self.failures = {}
        self.embedding_cache = LocalJsonCache(self.dir / "embedding_cache.json")
        self._load()

    def _load(self):
        for path, target in (
            (self.chunk_path, "records"),
            (self.vector_path, "vectors"),
        ):
            try:
                setattr(self, target, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                setattr(self, target, {})

    def refresh(self, embedding_client):
        chunks = {item.chunk_id: item for item in self.chunks_factory()}
        for ident in set(self.records) - set(chunks):
            self.records.pop(ident, None)
            self.vectors.pop(ident, None)
        embedded = 0
        pending = []
        for ident, chunk in chunks.items():
            previous = self.records.get(ident, {})
            if (
                previous.get("content_hash") != chunk.content_hash
                or ident not in self.vectors
            ):
                pending.append((ident, chunk))
            self.records[ident] = asdict(chunk)
        # Keep request bodies below managed embedding service limits.  Code
        # symbols can be much larger than a normal text chunk.
        for ident, chunk in pending:
            try:
                text = chunk.text[:2000]
                key = embedding_cache_key(getattr(embedding_client, "model", ""), text)
                vector = self.embedding_cache.get(key)
                if vector is None:
                    vector = embedding_client.embed(text)
                    self.embedding_cache.set(key, vector)
                self.vectors[ident] = vector
                embedded += 1
            except Exception as exc:
                self.failures[ident] = str(exc)
            self._save()
        self._save()
        return {"chunks": len(self.records), "embedded": embedded}

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            self.chunk_path, json.dumps(self.records, ensure_ascii=False)
        )
        self._atomic_write(self.vector_path, json.dumps(self.vectors))

    @staticmethod
    def _atomic_write(path, text):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2 * (attempt + 1))

    def search(self, vector, limit=20):
        ranked = sorted(
            (
                (ident, _cosine(vector, values))
                for ident, values in self.vectors.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            (CodeChunk(**self.records[ident]), score)
            for ident, score in ranked[:limit]
            if ident in self.records
        ]


def chunk_workspace(root, code_index, window=80, overlap=20):
    root = Path(root)
    chunks = []
    ignored = {
        ".git",
        ".codecub",
        ".workbuddy",
        "node_modules",
        "dist",
        "build",
        "venv",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".uv-cache",
        ".ruff_cache",
    }
    extensions = {".py", ".md", ".json", ".toml", ".yaml", ".yml", ".txt"}
    if shutil.which("rg"):
        result = subprocess.run(
            [
                "rg",
                "--files",
                "-g",
                "*.py",
                "-g",
                "*.md",
                "-g",
                "*.json",
                "-g",
                "*.toml",
                "-g",
                "*.yaml",
                "-g",
                "*.yml",
                "-g",
                "*.txt",
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
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        entries = [(root / line) for line in result.stdout.splitlines() if line]
    else:
        entries = []
        for directory, directory_names, file_names in os.walk(root):
            directory_names[:] = [
                name for name in directory_names if name not in ignored
            ]
            entries.extend(Path(directory) / filename for filename in file_names)
    for path in entries:
        if path.suffix not in extensions:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        ranges = (
            [
                (symbol.start_line, symbol.end_line, symbol.qualified_name)
                for symbol in code_index.file_outline(relative)
            ]
            if path.suffix == ".py"
            else []
        )
        if not ranges:
            ranges = [
                (start, min(start + window - 1, len(lines)), "")
                for start in range(1, len(lines) + 1, max(1, window - overlap))
            ]
        for start, end, symbol in ranges:
            text = "\n".join(lines[start - 1 : end])
            digest = hashlib.sha256(text.encode()).hexdigest()
            chunks.append(
                CodeChunk(
                    f"{relative}:{start}:{end}",
                    relative,
                    start,
                    end,
                    symbol,
                    digest,
                    text,
                )
            )
    return chunks
