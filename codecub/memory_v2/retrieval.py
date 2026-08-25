"""Memory Retrieval — bounded, layered Top-K over Evidence + Durable stores.

Retrieval runs at task start / blocker change / recovery turn (never per tool
step) and combines the user task with Working State signals (goal, relevant
symbols, changed files, blocker, latest verification failure) into a bounded
query. Ranking is layered (project scope, exact path/symbol, freshness,
keyword/tag, recency, past usefulness, memory type, status) with per-store
Top-K and an overall token budget. Stale evidence is returned as a
STALE—REVALIDATE location hint, never as current truth.

Fresh Evidence > Stale Evidence Hint > Durable Memory
(they answer different questions; durable facts are auxiliary).

Progress-aware: once a hint has been delivered and the model has since read
that path, the hint is marked used and not re-injected.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from ..memory import canonicalize_path
from . import secrets as secretlib

DEFAULT_EVIDENCE_TOP_K = 2
DEFAULT_DURABLE_TOP_K = 2
DEFAULT_OVERALL_LIMIT = 4
DEFAULT_TOKEN_BUDGET = 500  # model-visible memory tokens (chars when no counter)
MAX_QUERY_CHARS = 600
MAX_SYMBOLS_IN_QUERY = 8
MAX_FILES_IN_QUERY = 6

STALE_MARKER = "STALE—REVALIDATE"
FRESH_MARKER = "FRESH"
MISSING_MARKER = "MISSING"
ACTIVE_MARKER = "ACTIVE"
CONFLICT_MARKER = "CONFLICT"

EVIDENCE_KIND_WEIGHTS = {
    "source_location": 20,
    "symbol_location": 20,
    "test_command": 15,
    "verification_result": 10,
    "resolution": 5,
    "architecture_anchor": 5,
    "dependency_location": 10,
    "config_location": 10,
    "error_resolution": 15,
}
DURABLE_TOPIC_WEIGHTS = {
    "build-and-test": 20,
    "environment-constraints": 15,
    "validated-workflows": 15,
    "project-conventions": 10,
    "known-pitfalls": 10,
    "key-decisions": 5,
    "dependency-facts": 5,
    "user-preferences": 5,
}
STATUS_FRESH = "fresh"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_SUPERSEDED = "superseded"


def _tokenize(text):
    return set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", str(text).lower()))


def _clip(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "..."


@dataclass
class MemoryRetrievalItem:
    kind: str  # "evidence" | "durable"
    text: str
    marker: str
    record_id: str
    path: str = ""
    symbol: str = ""
    topic: str = ""
    status: str = ""
    reason: str = ""
    score: float = 0.0
    tokens: int = 0

    def to_dict(self):
        return {
            "kind": self.kind,
            "text": self.text,
            "marker": self.marker,
            "record_id": self.record_id,
            "path": self.path,
            "symbol": self.symbol,
            "topic": self.topic,
            "status": self.status,
            "reason": self.reason,
            "score": round(float(self.score), 1),
            "tokens": int(self.tokens),
        }


@dataclass
class MemoryRetrievalResult:
    query: str = ""
    items: list = field(default_factory=list)  # list[MemoryRetrievalItem]
    evidence_items: list = field(default_factory=list)
    durable_items: list = field(default_factory=list)
    stale_count: int = 0
    missing_count: int = 0
    suppressed_used_count: int = 0
    total_tokens: int = 0
    evidence_top_k: int = DEFAULT_EVIDENCE_TOP_K
    durable_top_k: int = DEFAULT_DURABLE_TOP_K
    token_budget: int = DEFAULT_TOKEN_BUDGET
    fingerprint: str = ""
    cached: bool = False

    def render(self):
        if not self.items:
            return ""
        lines = ["Relevant memory:"]
        if self.evidence_items:
            lines.append("Evidence:")
            for item in self.evidence_items:
                lines.append(f"- [{item.marker}] {item.text}")
        if self.durable_items:
            lines.append("Durable:")
            for item in self.durable_items:
                lines.append(f"- [{item.marker}] {item.text}")
        return "\n".join(lines)

    def to_dict(self):
        return {
            "query": self.query,
            "evidence_count": len(self.evidence_items),
            "durable_count": len(self.durable_items),
            "total_items": len(self.items),
            "stale_count": self.stale_count,
            "missing_count": self.missing_count,
            "suppressed_used_count": self.suppressed_used_count,
            "total_tokens": self.total_tokens,
            "fingerprint": self.fingerprint,
            "cached": self.cached,
            "items": [item.to_dict() for item in self.items],
        }


class MemoryRetriever:
    def __init__(
        self,
        evidence_store,
        durable_store,
        workspace_root,
        token_counter=None,
        evidence_top_k=DEFAULT_EVIDENCE_TOP_K,
        durable_top_k=DEFAULT_DURABLE_TOP_K,
        token_budget=DEFAULT_TOKEN_BUDGET,
        overall_limit=DEFAULT_OVERALL_LIMIT,
    ):
        self.evidence_store = evidence_store
        self.durable_store = durable_store
        self.workspace_root = workspace_root
        self.token_counter = token_counter
        self.evidence_top_k = int(evidence_top_k)
        self.durable_top_k = int(durable_top_k)
        self.token_budget = int(token_budget)
        self.overall_limit = int(overall_limit)
        # Progress-aware state.
        self.generation = 0  # incremented by the facade on any store mutation
        self.read_paths = set()  # paths read AFTER a hint was delivered
        self.delivered_paths = set()  # paths ever delivered as evidence hints
        self._last_fingerprint = ""
        self._last_result = None
        self.retrieval_count = 0
        self.retrieved_evidence_count = 0
        self.retrieved_durable_count = 0
        self.last_total_tokens = 0
        self.last_injected_tokens = 0

    # ------------------------------------------------------------------
    # Query construction (bounded; never the full history)
    # ------------------------------------------------------------------

    def build_query(self, user_message, working_state=None):
        parts = []
        user_text = str(user_message or "").strip()
        if user_text:
            parts.append(user_text)
        if working_state is not None:
            goal = str(getattr(working_state, "goal", "") or "").strip()
            if goal and goal != user_text:
                parts.append(f"goal: {goal[:200]}")
            symbols = []
            for item in (getattr(working_state, "relevant_symbols", None) or [])[:MAX_SYMBOLS_IN_QUERY]:
                path = str(item.get("path", "") or "")
                name = str(item.get("name", "") or "")
                if path or name:
                    symbols.append(f"{path}:{name}" if path and name else (path or name))
            if symbols:
                parts.append("symbols: " + ", ".join(symbols))
            files = []
            for path in (getattr(working_state, "changed_files", None) or [])[:MAX_FILES_IN_QUERY]:
                files.append(str(path))
            for item in (getattr(working_state, "relevant_symbols", None) or [])[:MAX_FILES_IN_QUERY]:
                path = str(item.get("path", "") or "")
                if path and path not in files:
                    files.append(path)
            if files:
                parts.append("files: " + ", ".join(files[:MAX_FILES_IN_QUERY]))
            blockers = (getattr(working_state, "blockers", None) or [])
            if blockers:
                parts.append(f"blocker: {str(blockers[-1].get('text', ''))[:200]}")
            verification = getattr(working_state, "verification", None) or []
            failures = [v for v in verification if v.get("status") != "ok"]
            if failures:
                last = failures[-1]
                parts.append(
                    f"last verification failure: {str(last.get('command', ''))[:120]}"
                )
        return _clip("\n".join(part for part in parts if part), MAX_QUERY_CHARS)

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def _score_evidence(self, record, query_tokens, query_text):
        path = str(record.get("path", "") or "")
        symbol = str(record.get("symbol", "") or "")
        summary = str(record.get("summary", "") or "")
        status = str(record.get("status", "") or STATUS_FRESH)
        path_tokens = _tokenize(path)
        symbol_tokens = _tokenize(symbol)
        summary_tokens = _tokenize(summary)
        score = 0.0
        reason = []
        matched = False
        if path and path in query_text:
            score += 300
            reason.append("exact_path")
            matched = True
        elif path_tokens & query_tokens:
            score += 20 * len(path_tokens & query_tokens)
            reason.append("path_tokens")
            matched = True
        if symbol and symbol.lower() in query_text.lower():
            score += 250
            reason.append("exact_symbol")
            matched = True
        elif symbol_tokens & query_tokens:
            score += 60
            reason.append("symbol_tokens")
            matched = True
        tag_matches = _tokenize(" ".join(record.get("tags") or [])) & query_tokens
        if tag_matches:
            score += 30
            reason.append("tags")
            matched = True
        keyword_matches = summary_tokens & query_tokens
        if keyword_matches:
            score += 15 * min(len(keyword_matches), 6)
            reason.append("keywords")
            matched = True
        if not matched:
            # 无任何相关性信号：不进入候选（即使 kind 有基础权重）。
            return None
        if status == STATUS_FRESH:
            score += 60
        # stale/missing still return as location hints (lower rank).
        score += EVIDENCE_KIND_WEIGHTS.get(str(record.get("kind", "")), 0)
        use_count = min(int(record.get("use_count") or 0), 2)
        score += 5 * use_count
        try:
            confidence = float(record.get("confidence") or 1.0)
        except (TypeError, ValueError):
            confidence = 1.0
        score *= max(0.1, min(1.0, confidence))
        return score, "; ".join(reason) or "keyword"

    def _score_durable(self, record, query_tokens, query_text):
        statement = str(record.get("statement", "") or "")
        topic = str(record.get("topic", "") or "")
        tags = _tokenize(" ".join(record.get("tags") or []))
        score = 0.0
        reason = []
        matched = False
        keyword_matches = _tokenize(statement) & query_tokens
        if keyword_matches:
            score += 15 * min(len(keyword_matches), 6)
            reason.append("keywords")
            matched = True
        tag_matches = tags & query_tokens
        if tag_matches:
            score += 30
            reason.append("tags")
            matched = True
        if not matched:
            return None
        score += DURABLE_TOPIC_WEIGHTS.get(topic, 0)
        use_count = min(int(record.get("use_count") or 0), 2)
        score += 5 * use_count
        if record.get("conflict_with"):
            score -= 40
            reason.append("conflict")
        try:
            confidence = float(record.get("confidence") or 1.0)
        except (TypeError, ValueError):
            confidence = 1.0
        score *= max(0.1, min(1.0, confidence))
        return score, "; ".join(reason) or "keyword"

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _select_evidence(self, query_text, query_tokens):
        scored = []
        seen_paths = set()
        for record in self.evidence_store.latest_records():
            path = str(record.get("path", "") or "")
            if record.get("status") == STATUS_SUPERSEDED:
                continue
            # Source diversity: at most one evidence per path in the selection.
            if path in seen_paths:
                continue
            seen_paths.add(path)
            scored_entry = self._score_evidence(record, query_tokens, query_text)
            if scored_entry is None:
                continue
            score, reason = scored_entry
            scored.append((score, record, reason))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[: self.evidence_top_k]

    def _select_durable(self, query_text, query_tokens):
        scored = []
        for record in self.durable_store.active_records():
            scored_entry = self._score_durable(record, query_tokens, query_text)
            if scored_entry is None:
                continue
            score, reason = scored_entry
            scored.append((score, record, reason))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[: self.durable_top_k]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(self, user_message, working_state=None, force=False):
        """Retrieve bounded memory; returns MemoryRetrievalResult.

        `force` bypasses the fingerprint cache (e.g. first retrieval of a run
        after the store changed).
        """
        query = self.build_query(user_message, working_state)
        fingerprint = self._fingerprint(query)
        if (
            not force
            and self._last_result is not None
            and self._last_fingerprint == fingerprint
        ):
            self._last_result.cached = True
            return self._last_result
        self.retrieval_count += 1
        query_tokens = _tokenize(query)
        evidence_selected = self._select_evidence(query, query_tokens)
        durable_selected = self._select_durable(query, query_tokens)

        items = []
        evidence_items = []
        durable_items = []
        stale_count = 0
        missing_count = 0
        suppressed = 0
        budget = self.token_budget
        used = 0

        for score, record, reason in evidence_selected:
            if used >= self.overall_limit:
                break
            status = str(record.get("status", "") or STATUS_FRESH)
            marker = {STATUS_FRESH: FRESH_MARKER, STATUS_STALE: STALE_MARKER, STATUS_MISSING: MISSING_MARKER}.get(status, FRESH_MARKER)
            path = str(record.get("path", "") or "")
            symbol = str(record.get("symbol", "") or "")
            summary = str(record.get("summary", "") or "")
            # Progress-aware: only suppress a path that was previously delivered
            # as a hint AND has since been read by the model (spec §72).
            if path and path in self.read_paths:
                suppressed += 1
                continue
            location = f"{path}:{symbol}" if path and symbol else path
            if not location:
                location = "(verified command)"
            text = f"{location} — {summary}"
            if status == STATUS_STALE:
                stale_count += 1
                text = f"{location} — {summary} ({STALE_MARKER}: re-read before relying on details)"
            elif status == STATUS_MISSING:
                missing_count += 1
                text = f"{location} — {summary} (may have moved or been deleted)"
            if secretlib.contains_secret(text):
                continue
            item = MemoryRetrievalItem(
                kind="evidence",
                text=_clip(text, 220),
                marker=marker,
                record_id=str(record.get("evidence_id", "")),
                path=path,
                symbol=symbol,
                status=status,
                reason=reason,
                score=score,
            )
            tokens = self._count(item.text)
            if used > 0 and budget - used < tokens:
                break
            items.append(item)
            evidence_items.append(item)
            used += tokens
            self.retrieved_evidence_count += 1
            if path:
                self.delivered_paths.add(path)

        for score, record, reason in durable_selected:
            if used >= self.overall_limit:
                break
            statement = str(record.get("statement", "") or "")
            topic = str(record.get("topic", "") or "")
            marker = CONFLICT_MARKER if record.get("conflict_with") else ACTIVE_MARKER
            text = f"{statement} ({topic})"
            if secretlib.contains_secret(text):
                continue
            item = MemoryRetrievalItem(
                kind="durable",
                text=_clip(text, 220),
                marker=marker,
                record_id=str(record.get("memory_id", "")),
                topic=topic,
                status=str(record.get("status", "") or "active"),
                reason=reason,
                score=score,
            )
            tokens = self._count(item.text)
            if used > 0 and budget - used < tokens:
                break
            items.append(item)
            durable_items.append(item)
            used += tokens
            self.retrieved_durable_count += 1

        result = MemoryRetrievalResult(
            query=query,
            items=items,
            evidence_items=evidence_items,
            durable_items=durable_items,
            stale_count=stale_count,
            missing_count=missing_count,
            suppressed_used_count=suppressed,
            total_tokens=used,
            evidence_top_k=self.evidence_top_k,
            durable_top_k=self.durable_top_k,
            token_budget=self.token_budget,
            fingerprint=fingerprint,
        )
        self.last_total_tokens = used
        self.last_injected_tokens = used
        self._last_fingerprint = fingerprint
        self._last_result = result
        return result

    def mark_read(self, path):
        """Progress-aware: record that the model read `path` after a hint.

        Only paths that were previously delivered as hints can be suppressed;
        a read that happened before any hint never suppresses later retrieval
        (the hint may still guide the next session / next task).
        """
        canonical = canonicalize_path(path, self.workspace_root)
        if canonical and canonical in self.delivered_paths:
            self.read_paths.add(canonical)

    def note_store_generation(self):
        """Called by the facade after any mutation: invalidates the cache."""
        self.generation += 1
        self._last_fingerprint = ""
        self._last_result = None

    def _fingerprint(self, query):
        return hashlib.sha256(
            f"{query}|gen={self.generation}|read={sorted(self.read_paths)}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]

    def _count(self, text):
        if self.token_counter is not None:
            try:
                return max(1, int(self.token_counter.count(text)))
            except Exception:
                return max(1, len(str(text)))
        return max(1, len(str(text)))
