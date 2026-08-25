"""Small local JSON cache; callers own invalidation namespace construction."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Protocol


class CacheBackend(Protocol):
    def get(self, key): ...
    def set(self, key, value): ...


class LocalJsonCache:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def key(*parts):
        return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False), encoding="utf-8"
        )


def embedding_cache_key(model, text):
    return LocalJsonCache.key("embedding", model, hashlib.sha256(text.encode()).hexdigest())


def retrieval_cache_key(
    workspace_fingerprint, query, path, retrieval_config, embedding_model, reranker_config
):
    return LocalJsonCache.key(
        "retrieval", workspace_fingerprint, query, path, retrieval_config,
        embedding_model, reranker_config,
    )


def file_summary_cache_key(path, content_hash, model, policy_version):
    return LocalJsonCache.key("file_summary", path, content_hash, model, policy_version)


def semantic_answer_cache_allowed(read_only, workspace_changed, multi_turn):
    return bool(read_only and not workspace_changed and not multi_turn)
