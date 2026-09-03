"""Hybrid lexical, AST, optional semantic and optional reranked code retrieval."""

from __future__ import annotations

import os
import hashlib
import re
import time
import json
import urllib.request
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from .embeddings import OpenAICompatibleEmbeddingClient
from .vector_index import LocalVectorIndex, chunk_workspace
from .cache import LocalJsonCache, retrieval_cache_key


@dataclass(frozen=True)
class RetrievalHit:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float
    sources: tuple[str, ...]
    symbol: str = ""


@dataclass(frozen=True)
class RetrievalQuery:
    """A small, deterministic interpretation of a code-retrieval query."""

    raw_query: str
    intent: str
    symbol: str = ""
    kind: str = ""


def parse_retrieval_query(query: str) -> RetrievalQuery:
    """Extract definition/reference intents without an LLM or NLP dependency."""

    raw_query = str(query)
    normalized = " ".join(raw_query.strip().split())
    lowered = normalized.lower()
    identifier = r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)"

    definition_patterns = (
        (rf"\bclass\s+definition\s+(?:of\s+)?{identifier}\b", "class"),
        (rf"\bfunction\s+definition\s+(?:of\s+)?{identifier}\b", "function"),
        (rf"\bwhere\s+is\s+{identifier}\s+defined\b", ""),
        (rf"\bdefinition\s+of\s+{identifier}\b", ""),
        (rf"\b{identifier}\s+definition\b", ""),
    )
    for pattern, kind in definition_patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            return RetrievalQuery(raw_query, "definition", match.group(1), kind)

    reference_patterns = (
        rf"\bcalls\s+to\s+{identifier}\b",
        rf"\breferences\s+to\s+{identifier}\b",
        rf"\bwhere\s+is\s+{identifier}\s+called\b",
        rf"\bwho\s+calls\s+{identifier}\b",
    )
    for pattern in reference_patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            return RetrievalQuery(raw_query, "reference", match.group(1), "")
    return RetrievalQuery(raw_query, "generic")


@dataclass
class RetrievalResult:
    query: str
    strategy: str
    hits: list[RetrievalHit]
    filtered_out: list = field(default_factory=list)
    semantic_applied: bool = False
    rerank_applied: bool = False
    elapsed_ms: int = 0
    cache_hit: bool = False
    retrieval_route: str = ""
    semantic_skipped_reason: str = "none"
    score_metadata: dict[str, dict[str, float | int | None]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class CandidateLimits:
    lexical: int = 8
    ast: int = 8
    semantic_recall: int = 20

    @property
    def semantic(self):
        """Backward-compatible name for the semantic recall depth."""

        return self.semantic_recall


@dataclass(frozen=True)
class FusionWeights:
    lexical: float = 0.7
    ast: float = 0.8
    semantic: float = 1.0
    rerank: float = 1.1
    path_affinity: float = 0.004
    symbol_affinity: float = 0.004


_STOP_WORDS = frozenset({"where", "is", "the", "of", "to", "in", "a", "an"})


def _normalized_tokens(value: str) -> set[str]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value))
    return {
        token.lower()
        for token in re.split(r"[\\/_\-.\s]+", camel_split)
        if token and token.lower() not in _STOP_WORDS
    }


def normalize_query_tokens(query: str) -> set[str]:
    return _normalized_tokens(query)


def normalize_path_tokens(path: str) -> set[str]:
    return _normalized_tokens(path)


def path_affinity_score(query: str, path: str) -> float:
    """Return query-token coverage by a normalized repository path."""

    query_tokens = normalize_query_tokens(query)
    path_tokens = normalize_path_tokens(path)
    return len(query_tokens & path_tokens) / len(query_tokens) if query_tokens else 0.0


def symbol_affinity_score(query: str, symbol: str) -> float:
    """Return query-token coverage by a normalized symbol name."""

    query_tokens = normalize_query_tokens(query)
    return len(query_tokens & _normalized_tokens(symbol)) / len(query_tokens) if query_tokens and symbol else 0.0


def fuse_candidate_evidence(candidate: dict, query: str, weights: FusionWeights):
    """Combine independent retrieval ranks with bounded path/symbol evidence."""

    ranks = candidate.get("ranks", {})
    components = {
        source: (getattr(weights, source) / (60 + rank) if rank else 0.0)
        for source, rank in (
            ("lexical", ranks.get("lexical")),
            ("ast", ranks.get("ast")),
            ("semantic", ranks.get("semantic")),
            ("rerank", ranks.get("rerank")),
        )
    }
    path_score = path_affinity_score(query, candidate["path"])
    symbol_score = symbol_affinity_score(query, candidate.get("symbol", ""))
    components["path_affinity"] = weights.path_affinity * path_score
    components["symbol_affinity"] = weights.symbol_affinity * symbol_score
    return sum(components.values()), {
        "final_score": sum(components.values()),
        "lexical_component": components["lexical"],
        "ast_component": components["ast"],
        "semantic_component": components["semantic"],
        "rerank_component": components["rerank"],
        "path_affinity": path_score,
        "symbol_affinity": symbol_score,
    }


def select_pre_rerank_candidates(candidates, query, limit, weights):
    """Select a bounded rerank batch while protecting strong non-rerank evidence."""

    for candidate in candidates:
        pre_score, _ = fuse_candidate_evidence(candidate, query, weights)
        reasons = []
        path_score = path_affinity_score(query, candidate["path"])
        symbol_score = symbol_affinity_score(query, candidate.get("symbol", ""))
        ranks = candidate.get("ranks", {})
        if path_score >= 0.5:
            reasons.append("path_affinity")
        if symbol_score >= 0.5:
            reasons.append("symbol_affinity")
        if ranks.get("lexical") and ranks.get("semantic"):
            reasons.append("lexical_semantic")
        if ranks.get("ast"):
            reasons.append("ast_evidence")
        candidate["debug"] = {
            "semantic_recall_rank": ranks.get("semantic"),
            "pre_rerank_score": pre_score,
            "protected": bool(reasons),
            "protection_reason": ",".join(reasons) if reasons else "none",
            "selected_for_rerank": False,
        }

    protected = sorted(
        (candidate for candidate in candidates if candidate["debug"]["protected"]),
        key=lambda item: item["debug"]["pre_rerank_score"], reverse=True,
    )[: min(2, limit)]
    selected = list(protected)
    selected_keys = {(item["path"], item["start"], item["end"]) for item in selected}
    for candidate in candidates:
        key = (candidate["path"], candidate["start"], candidate["end"])
        if len(selected) >= limit:
            break
        if key not in selected_keys:
            selected.append(candidate)
            selected_keys.add(key)
    for candidate in selected:
        candidate["debug"]["selected_for_rerank"] = True
    return selected


class Reranker(Protocol):
    def rerank(self, query: str, documents: list[dict]) -> list[dict]: ...


class HybridRetriever:
    def __init__(
        self, root, code_index, embedding_client=None, reranker=None,
        rerank_candidate_limit=8, candidate_limits=None, fusion_weights=None,
    ):
        self.root, self.code_index = root, code_index
        # ``False`` is an intentional opt-out for controlled ablations; ``None``
        # means use the workspace configuration.
        self.reranker = self._reranker_from_env() if reranker is None else reranker
        self.embedding_client = self._from_env() if embedding_client is None else embedding_client
        self.vector_index = (
            LocalVectorIndex(root, lambda: chunk_workspace(root, code_index))
            if self.embedding_client
            else None
        )
        self.cache = LocalJsonCache(self.root / ".codecub" / "cache" / "retrieval.json")
        self.rerank_candidate_limit = max(1, int(rerank_candidate_limit))
        self.candidate_limits = candidate_limits or CandidateLimits()
        self.fusion_weights = fusion_weights or FusionWeights()
        self._workspace_state = None
        self._cached_workspace_fingerprint = ""
        self._workspace_hash_computations = 0

    def _workspace_paths(self):
        if shutil.which("rg"):
            command = [
                "rg", "--files", "-g", "!**/.codecub/**", "-g", "!**/.workbuddy/**",
                "-g", "!**/.uv-cache/**", "-g", "!**/.venv/**", "-g", "!**/venv/**",
            ]
            output = subprocess.run(
                command, cwd=self.root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False,
            ).stdout
            return [self.root / line for line in output.splitlines() if line]
        return [path for path in self.root.rglob("*") if path.is_file()]

    def _workspace_fingerprint(self, paths):
        state = []
        for path in paths:
            try:
                stat = path.stat()
                state.append(
                    (path.relative_to(self.root).as_posix(), stat.st_mtime_ns, stat.st_size)
                )
            except OSError:
                continue
        state = tuple(sorted(state))
        if state == self._workspace_state:
            return self._cached_workspace_fingerprint
        digest = hashlib.sha256()
        for path in sorted(paths):
            try:
                digest.update(path.relative_to(self.root).as_posix().encode())
                digest.update(hashlib.sha256(path.read_bytes()).digest())
            except OSError:
                continue
        self._workspace_state = state
        self._cached_workspace_fingerprint = digest.hexdigest()
        self._workspace_hash_computations += 1
        return self._cached_workspace_fingerprint

    @staticmethod
    def _from_cached(query, payload):
        return RetrievalResult(
            query=query,
            strategy=payload["strategy"],
            hits=[
                RetrievalHit(**{**item, "sources": tuple(item["sources"])})
                for item in payload["hits"]
            ],
            filtered_out=list(payload.get("filtered_out", [])),
            semantic_applied=bool(payload.get("semantic_applied")),
            rerank_applied=bool(payload.get("rerank_applied")),
            elapsed_ms=0,
            cache_hit=True,
            retrieval_route=payload.get("retrieval_route", ""),
            semantic_skipped_reason=payload.get("semantic_skipped_reason", "none"),
            score_metadata=dict(payload.get("score_metadata", {})),
        )

    @staticmethod
    def _from_env():
        base, key, model = (
            os.environ.get(name, "").strip()
            for name in (
                "CODECUB_EMBEDDING_BASE_URL",
                "CODECUB_EMBEDDING_API_KEY",
                "CODECUB_EMBEDDING_MODEL",
            )
        )
        return (
            OpenAICompatibleEmbeddingClient(base, key, model)
            if base and key and model
            else None
        )

    @staticmethod
    def _reranker_from_env():
        url = os.environ.get("CODECUB_RERANK_URL", "").strip()
        key = os.environ.get("CODECUB_RERANK_API_KEY", "").strip()
        model = os.environ.get("CODECUB_RERANK_MODEL", "qwen3-rerank").strip()
        return OpenAICompatibleReranker(url, key, model) if url and key else None

    def _read_snippet(self, path, start_line, end_line, context=0):
        try:
            lines = (self.root / path).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return "", start_line, end_line
        start = max(1, int(start_line) - context)
        end = min(len(lines), int(end_line) + context)
        return "\n".join(lines[start - 1 : end]), start, end

    def _query_embedding(self, query):
        normalized = " ".join(str(query).lower().split())
        model = getattr(self.embedding_client, "model", "")
        key = LocalJsonCache.key("query_embedding", model, normalized)
        vector = self.cache.get(key)
        if vector is None:
            vector = self.embedding_client.embed(query)
            self.cache.set(key, vector)
        return vector

    @staticmethod
    def _dedupe_files(candidates):
        """Keep one representative hit per file while retaining its provenance."""

        merged = {}
        for item in candidates:
            existing = merged.get(item["path"])
            if existing is None:
                merged[item["path"]] = dict(item, sources=set(item["sources"]))
                continue
            existing["sources"].update(item["sources"])
            existing["score"] = max(existing["score"], item["score"])
            for source, rank in item.get("ranks", {}).items():
                previous = existing.setdefault("ranks", {}).get(source)
                if previous is None or rank < previous:
                    existing["ranks"][source] = rank
            if item.get("structural_exact") and not existing.get("structural_exact"):
                replacement = dict(item, sources=existing["sources"])
                merged[item["path"]] = replacement
            elif not existing.get("symbol") and item.get("symbol"):
                existing["symbol"] = item["symbol"]
        return list(merged.values())

    def retrieve(self, query, limit=5):
        began, candidates, filtered = time.monotonic(), {}, []
        parsed_query = parse_retrieval_query(query)
        paths = self._workspace_paths()
        workspace_fingerprint = self._workspace_fingerprint(paths)
        cache_key = retrieval_cache_key(
            workspace_fingerprint,
            query,
            "",
            (
                f"rrf-v4-fusion-{self.rerank_candidate_limit}-"
                f"{self.candidate_limits.lexical}-{self.candidate_limits.ast}-"
                f"{self.candidate_limits.semantic_recall}-{self.fusion_weights}"
            ),
            getattr(self.embedding_client, "model", ""),
            getattr(self.reranker, "model", ""),
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            return self._from_cached(query, cached)

        def add(path, start, end, text, source, rank, symbol="", structural_exact=False):
            key = (path, start, end)
            item = candidates.setdefault(
                key,
                {
                    "path": path,
                    "start": start,
                    "end": end,
                    "text": text,
                    "symbol": symbol,
                    "score": 0.0,
                    "sources": set(),
                    "ranks": {},
                    "structural_exact": structural_exact,
                },
            )
            item["score"] += 1 / (60 + rank)
            item["sources"].add(source)
            evidence_source = "ast" if source.startswith("ast") else source
            previous_rank = item["ranks"].get(evidence_source)
            if previous_rank is None or rank < previous_rank:
                item["ranks"][evidence_source] = rank
            item["structural_exact"] = item["structural_exact"] or structural_exact

        words = [word for word in re.findall(r"\w+", query.lower()) if len(word) > 2]
        lexical_rank = 0
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                filtered.append({"path": str(path), "reason": "unreadable"})
                continue
            for number, line in enumerate(lines, 1):
                if words and all(word in line.lower() for word in words):
                    lexical_rank += 1
                    add(
                        path.relative_to(self.root).as_posix(),
                        number,
                        number,
                        line,
                        "lexical",
                        lexical_rank,
                    )
                    if (
                        parsed_query.intent == "generic"
                        and lexical_rank >= self.candidate_limits.lexical
                    ):
                        break
            if (
                parsed_query.intent == "generic"
                and lexical_rank >= self.candidate_limits.lexical
            ):
                break
        if parsed_query.intent == "definition":
            exact_symbols = self.code_index.symbol_search(
                parsed_query.symbol, kind=parsed_query.kind, limit=20
            )
            for rank, symbol in enumerate(exact_symbols, 1):
                if symbol.name.lower() != parsed_query.symbol.lower() and (
                    symbol.qualified_name.lower() != parsed_query.symbol.lower()
                ):
                    continue
                text, start, end = self._read_snippet(
                    symbol.path, symbol.start_line, symbol.end_line
                )
                if text:
                    add(
                        symbol.path, start, end, text, "ast_exact", rank,
                        symbol.qualified_name, structural_exact=True,
                    )
        elif parsed_query.intent == "reference":
            for rank, (path, line) in enumerate(
                self.code_index.find_references(parsed_query.symbol), 1
            ):
                text, start, end = self._read_snippet(path, line, line, context=2)
                if text:
                    add(path, start, end, text, "ast_reference", rank, parsed_query.symbol)
        else:
            for rank, symbol in enumerate(
                self.code_index.symbol_search(query, limit=self.candidate_limits.ast), 1
            ):
                text, start, end = self._read_snippet(
                    symbol.path, symbol.start_line, symbol.end_line
                )
                if text:
                    add(symbol.path, start, end, text, "ast", rank, symbol.qualified_name)
        high_confidence = (
            parsed_query.intent == "definition"
            and any("ast_exact" in item["sources"] for item in candidates.values())
        ) or (
            parsed_query.intent == "reference"
            and any("ast_reference" in item["sources"] for item in candidates.values())
        )
        semantic_skipped_reason = (
            "exact_definition"
            if high_confidence and parsed_query.intent == "definition"
            else "resolved_reference"
            if high_confidence and parsed_query.intent == "reference"
            else "none"
        )
        semantic = False
        semantic_attempted = False
        if self.vector_index and not high_confidence:
            semantic_attempted = True
            try:
                self.vector_index.refresh(self.embedding_client)
                vector = self._query_embedding(query)
                for rank, (chunk, score) in enumerate(
                    self.vector_index.search(
                        vector, self.candidate_limits.semantic_recall
                    ), 1
                ):
                    add(
                        chunk.path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.text,
                        "semantic",
                        rank,
                        chunk.symbol,
                    )
                semantic = True
            except Exception as exc:
                filtered.append(
                    {"path": "", "reason": "semantic_error", "detail": str(exc)}
                )
        structural_exact = [item for item in candidates.values() if item["structural_exact"]]
        ranked = sorted(
            (item for item in candidates.values() if not item["structural_exact"]),
            key=lambda item: item["score"], reverse=True,
        )
        ranked = self._dedupe_files(ranked)
        if parsed_query.intent == "generic":
            for item in ranked:
                item["score"], _ = fuse_candidate_evidence(
                    item, parsed_query.raw_query, self.fusion_weights
                )
            ranked.sort(key=lambda item: item["score"], reverse=True)
        reranked = False
        if self.reranker and ranked and not high_confidence:
            try:
                rerank_inputs = select_pre_rerank_candidates(
                    ranked,
                    parsed_query.raw_query,
                    self.rerank_candidate_limit,
                    self.fusion_weights,
                )
                reranked_items = self.reranker.rerank(query, rerank_inputs)
                reranked_keys = {
                    (item["path"], item["start"], item["end"])
                    for item in reranked_items
                }
                reranked_items.extend(
                    item
                    for item in rerank_inputs
                    if (item["path"], item["start"], item["end"]) not in reranked_keys
                )
                for rank, item in enumerate(reranked_items, 1):
                    item.setdefault("ranks", {})["rerank"] = rank
                ranked = reranked_items + ranked[len(rerank_inputs) :]
                reranked = True
            except Exception as exc:
                filtered.append(
                    {"path": "", "reason": "reranker_error", "detail": str(exc)}
                )
        ranked = self._dedupe_files(
            sorted(structural_exact, key=lambda item: item["score"], reverse=True)
            + ranked
        )
        score_metadata = {}
        if parsed_query.intent == "generic":
            for item in ranked:
                item["score"], score_metadata[item["path"]] = fuse_candidate_evidence(
                    item, parsed_query.raw_query, self.fusion_weights
                )
                score_metadata[item["path"]].update(item.get("debug", {}))
            ranked.sort(key=lambda item: item["score"], reverse=True)
        selected, discarded = ranked[:limit], ranked[limit:]
        filtered.extend(
            {"path": item["path"], "reason": "low_rank"} for item in discarded[:10]
        )
        strategy = (
            "ast_exact_lexical"
            if parsed_query.intent == "definition"
            else "ast_reference_lexical"
            if parsed_query.intent == "reference"
            else "semantic_rerank"
            if semantic and reranked
            else "semantic_only"
            if semantic
            else "lexical_ast_rrf"
        )
        retrieval_route = (
            "structural_fast_path"
            if high_confidence
            else "full_semantic"
            if semantic
            else "semantic_fallback"
            if semantic_attempted or self.vector_index
            else "lexical_ast_rrf"
        )
        result = RetrievalResult(
            query,
            strategy,
            [
                RetrievalHit(
                    item["path"],
                    item["start"],
                    item["end"],
                    item["text"],
                    item["score"],
                    tuple(sorted(item["sources"])),
                    item["symbol"],
                )
                for item in selected
            ],
            filtered[:10],
            semantic,
            reranked,
            int((time.monotonic() - began) * 1000),
            False,
            retrieval_route,
            semantic_skipped_reason,
            score_metadata,
        )
        self.cache.set(
            cache_key,
            {
                "strategy": result.strategy,
                "hits": [
                    {
                        "path": hit.path,
                        "start_line": hit.start_line,
                        "end_line": hit.end_line,
                        "text": hit.text,
                        "score": hit.score,
                        "sources": hit.sources,
                        "symbol": hit.symbol,
                    }
                    for hit in result.hits
                ],
                "filtered_out": result.filtered_out,
                "semantic_applied": result.semantic_applied,
                "rerank_applied": result.rerank_applied,
                "retrieval_route": result.retrieval_route,
                "semantic_skipped_reason": result.semantic_skipped_reason,
                "score_metadata": result.score_metadata,
            },
        )
        return result


class OpenAICompatibleReranker:
    """Adapter for DashScope's compatible ``/reranks`` endpoint."""

    def __init__(self, url, api_key, model, timeout=30):
        self.url, self.api_key, self.model, self.timeout = url, api_key, model, timeout

    def rerank(self, query, documents):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(
                {
                    "model": self.model,
                    "query": query,
                    "documents": [item["text"] for item in documents],
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode())
        results = payload.get("results", [])
        ordered = []
        for result in results:
            index = int(result.get("index", -1))
            if 0 <= index < len(documents):
                item = dict(documents[index])
                item["score"] = float(result.get("relevance_score", item["score"]))
                ordered.append(item)
        return ordered or documents
