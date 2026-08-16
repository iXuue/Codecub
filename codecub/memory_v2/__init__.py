"""Memory 2.0 facade — the single integration point used by the runtime.

Owns the Evidence Store, Durable Memory Store, Extractor, Consolidator,
Retriever and Migration, plus the observability counters and trace emission.
The runtime talks to this facade only; legacy `LayeredMemory` stays as the
v1 compatibility adapter used when `memory_v2` is disabled.

First principle: Live Workspace > Evidence Store > Durable Memory.
Memory is an index and navigation aid, never the source of truth.
"""

from __future__ import annotations

from pathlib import Path

from .consolidation import (
    ACTION_CONFLICT,
    ACTION_DUPLICATE,
    ACTION_MERGE,
    ACTION_NEW,
    ACTION_REJECT,
    ACTION_SUPERSEDE,
    MemoryConsolidator,
)
from .durable import DurableMemoryStore
from .evidence import EvidenceStore
from .extraction import MemoryExtractor
from .migration import MemoryMigration
from .observability import (
    EVENT_CANDIDATE_CONSOLIDATED,
    EVENT_CANDIDATE_EXTRACTED,
    EVENT_CANDIDATE_REJECTED,
    EVENT_CONFLICT_DETECTED,
    EVENT_EVIDENCE_REVALIDATED,
    EVENT_EVIDENCE_STALE,
    EVENT_MIGRATION_FINISHED,
    EVENT_MIGRATION_STARTED,
    EVENT_RETRIEVAL_FINISHED,
    EVENT_RETRIEVAL_STARTED,
    EVENT_SUPERSEDED,
    EVENT_WRITTEN,
    METRIC_CANDIDATE_COUNT,
    METRIC_CANDIDATE_PROMOTED_COUNT,
    METRIC_CANDIDATE_REJECTED_COUNT,
    METRIC_CONFLICT_COUNT,
    METRIC_DUPLICATE_COUNT,
    METRIC_DURABLE_STORE_SIZE,
    METRIC_EVIDENCE_STORE_SIZE,
    METRIC_INJECTED_TOKENS,
    METRIC_IRRELEVANT_RETRIEVAL_COUNT,
    METRIC_MEMORY_GUIDED_REREAD_COUNT,
    METRIC_REVALIDATED_EVIDENCE_COUNT,
    METRIC_RETRIEVAL_COUNT,
    METRIC_RETRIEVAL_TOKENS,
    METRIC_RETRIEVED_DURABLE_COUNT,
    METRIC_RETRIEVED_EVIDENCE_COUNT,
    METRIC_STALE_EVIDENCE_COUNT,
    METRIC_STALE_USED_WITHOUT_REVALIDATION,
    METRIC_SUPERSEDED_COUNT,
)
from .retrieval import MemoryRetriever


class MemoryV2:
    def __init__(
        self,
        workspace_root,
        token_counter=None,
        trace=None,
        task_id="",
        run_id="",
        evidence_top_k=2,
        durable_top_k=2,
        token_budget=500,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.trace = trace or (lambda _event, _payload=None: None)
        self.task_id = str(task_id or "")
        self.run_id = str(run_id or "")
        self.evidence_store = EvidenceStore(self.workspace_root)
        self.durable_store = DurableMemoryStore(self.workspace_root)
        self.consolidator = MemoryConsolidator(self.durable_store)
        self.extractor = MemoryExtractor(task_id=self.task_id, run_id=self.run_id)
        self.migration = MemoryMigration(self.workspace_root)
        self.retriever = MemoryRetriever(
            self.evidence_store,
            self.durable_store,
            self.workspace_root,
            token_counter=token_counter,
            evidence_top_k=evidence_top_k,
            durable_top_k=durable_top_k,
            token_budget=token_budget,
        )
        # Counters (report["memory_v2"]).
        self.counters = {
            METRIC_CANDIDATE_COUNT: 0,
            METRIC_CANDIDATE_REJECTED_COUNT: 0,
            METRIC_CANDIDATE_PROMOTED_COUNT: 0,
            METRIC_DUPLICATE_COUNT: 0,
            METRIC_SUPERSEDED_COUNT: 0,
            METRIC_CONFLICT_COUNT: 0,
            METRIC_STALE_EVIDENCE_COUNT: 0,
            METRIC_REVALIDATED_EVIDENCE_COUNT: 0,
            METRIC_IRRELEVANT_RETRIEVAL_COUNT: 0,
            METRIC_STALE_USED_WITHOUT_REVALIDATION: 0,
            METRIC_MEMORY_GUIDED_REREAD_COUNT: 0,
        }
        self.last_migration = None
        self._injected_hint_paths = set()
        self._injected_stale_paths = set()

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def migrate_legacy(self, legacy_session_memory=None):
        if self.migration.already_migrated():
            self.last_migration = self.migration.migrate(legacy_session_memory)
            return self.last_migration
        self.trace(EVENT_MIGRATION_STARTED, {})
        self.last_migration = self.migration.migrate(legacy_session_memory)
        # Migration writes through its own store instances; reload ours so the
        # facade's in-memory stores see the migrated records.
        self.evidence_store.load()
        self.durable_store.load()
        self.trace(
            EVENT_MIGRATION_FINISHED,
            {
                "migrated_evidence": self.last_migration.migrated_evidence,
                "migrated_durable": self.last_migration.migrated_durable,
            },
        )
        self._note_mutation()
        return self.last_migration

    def set_run_context(self, task_id="", run_id=""):
        """Per-run provenance context (extractor + evidence records)."""
        self.task_id = str(task_id or "")
        self.run_id = str(run_id or "")
        self.extractor.task_id = self.task_id
        self.extractor.run_id = self.run_id

    def reset_run_state(self):
        """Run-local state reset (per ask()); stores persist cross-session."""
        for key in list(self.counters):
            self.counters[key] = 0
        self.retriever.retrieval_count = 0
        self.retriever.retrieved_evidence_count = 0
        self.retriever.retrieved_durable_count = 0
        self.retriever.last_total_tokens = 0
        self.retriever.last_injected_tokens = 0
        self.retriever.read_paths = set()
        self.retriever.delivered_paths = set()
        self.retriever._last_fingerprint = ""
        self.retriever._last_result = None
        self._injected_hint_paths = set()
        self._injected_stale_paths = set()

    # ------------------------------------------------------------------
    # Evidence writes (objective events)
    # ------------------------------------------------------------------

    def record_tool_evidence(
        self, name, args, result_text, metadata=None, tool_call_id="", event_id=""
    ):
        """Feed objective tool events into the Evidence Store."""
        if name == "read_file":
            path = str(args.get("path") or "").strip()
            if path:
                revalidated = self.evidence_store.revalidate_path(path)
                if revalidated:
                    self.counters[METRIC_REVALIDATED_EVIDENCE_COUNT] += revalidated
                    self.trace(
                        EVENT_EVIDENCE_REVALIDATED,
                        {"path": self.evidence_store.canonical_path(path), "count": revalidated},
                    )
                    self._note_mutation()
        created = self.evidence_store.record_tool_evidence(
            name,
            args,
            result_text,
            metadata=metadata,
            task_id=self.task_id,
            run_id=self.run_id,
            tool_call_id=tool_call_id,
            event_id=event_id,
        )
        if created:
            self._note_mutation()
        return created

    def note_read(self, path):
        """Progress-aware: mark a path read (suppresses re-injection)."""
        self.retriever.mark_read(path)

    def refresh_freshness(self):
        """Recompute evidence statuses against the live workspace."""
        stale, missing = self.evidence_store.refresh_freshness()
        stale_records = stale + missing
        if stale_records:
            self.counters[METRIC_STALE_EVIDENCE_COUNT] += len(stale_records)
            self.trace(
                EVENT_EVIDENCE_STALE,
                {
                    "stale_count": len(stale),
                    "missing_count": len(missing),
                    "paths": [record["path"] for record in stale_records[:20]],
                },
            )
            self._note_mutation()
        return stale, missing

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(self, user_message, working_state=None, force=False):
        result = self.retriever.retrieve(user_message, working_state, force=force)
        if not result.cached:
            self.trace(EVENT_RETRIEVAL_STARTED, {})
            # Mark delivered evidence used so repeated identical hints are not
            # re-injected once the model has acted on them.
            for item in result.evidence_items:
                self.evidence_store.mark_used(item.record_id)
            self._injected_hint_paths.update(
                item.path for item in result.evidence_items if item.path
            )
            self._injected_stale_paths.update(
                item.path for item in result.evidence_items if item.path and item.status == "stale"
            )
            self.trace(
                EVENT_RETRIEVAL_FINISHED,
                {
                    "evidence_count": len(result.evidence_items),
                    "durable_count": len(result.durable_items),
                    "stale_count": result.stale_count,
                    "tokens": result.total_tokens,
                },
            )
        return result

    def finalize_run(self):
        """Run-end accounting: stale hints delivered but never re-read.

        Spec §68/§80: stale evidence must be revalidated before use; if the
        model received a stale hint and never read that path, that is a
        stale-used-without-revalidation event.
        """
        unrevalidated = sorted(self._injected_stale_paths - self.retriever.read_paths)
        if unrevalidated:
            self.counters[METRIC_STALE_USED_WITHOUT_REVALIDATION] += len(unrevalidated)
        guided = sorted(self._injected_hint_paths & self.retriever.read_paths)
        if guided:
            self.counters[METRIC_MEMORY_GUIDED_REREAD_COUNT] += len(guided)
        return {
            "stale_unrevalidated_paths": unrevalidated,
            "guided_reread_paths": guided,
        }

    # ------------------------------------------------------------------
    # Extraction + Consolidation + Persistence (task/milestone boundary)
    # ------------------------------------------------------------------

    def extract_and_persist(
        self, working_state, user_message, final_answer, run_id=""
    ):
        """Run extraction at task end and persist via the consolidator.

        Returns (promoted, rejections, superseded, conflicts, duplicates).
        """
        run_id = run_id or self.run_id
        candidates, rejections = self.extractor.extract_all(
            working_state,
            self.evidence_store,
            user_message,
            final_answer,
            run_id=run_id,
        )
        for candidate in candidates:
            self.trace(
                EVENT_CANDIDATE_EXTRACTED,
                {
                    "candidate_type": candidate.candidate_type,
                    "statement": candidate.statement[:200],
                },
            )
        self.counters[METRIC_CANDIDATE_COUNT] += len(candidates)
        for candidate_type, reason, statement in rejections:
            self.counters[METRIC_CANDIDATE_REJECTED_COUNT] += 1
            self.trace(
                EVENT_CANDIDATE_REJECTED,
                {
                    "candidate_type": candidate_type,
                    "reason": reason,
                    "statement": statement[:200],
                },
            )

        promoted = []
        superseded = []
        conflicts = []
        duplicates = []
        for candidate in candidates:
            outcome = self.consolidator.apply(
                candidate, source_task_id=self.task_id, source_run_id=run_id
            )
            if outcome.action == ACTION_REJECT:
                self.counters[METRIC_CANDIDATE_REJECTED_COUNT] += 1
                self.trace(
                    EVENT_CANDIDATE_REJECTED,
                    {
                        "candidate_type": candidate.candidate_type,
                        "reason": outcome.reason,
                        "statement": candidate.statement[:200],
                    },
                )
                continue
            if outcome.action in (ACTION_NEW, ACTION_MERGE):
                self.counters[METRIC_CANDIDATE_PROMOTED_COUNT] += 1
                promoted.append(outcome.record.memory_id)
                self.trace(
                    EVENT_CANDIDATE_CONSOLIDATED,
                    {
                        "action": outcome.action,
                        "topic": outcome.record.topic,
                        "memory_id": outcome.record.memory_id,
                    },
                )
                self.trace(
                    EVENT_WRITTEN,
                    {
                        "kind": "durable",
                        "topic": outcome.record.topic,
                        "statement": outcome.record.statement[:200],
                    },
                )
            elif outcome.action == ACTION_DUPLICATE:
                self.counters[METRIC_DUPLICATE_COUNT] += 1
                duplicates.append(outcome.record.memory_id)
                self.trace(
                    EVENT_CANDIDATE_CONSOLIDATED,
                    {"action": "DUPLICATE", "memory_id": outcome.record.memory_id},
                )
            elif outcome.action == ACTION_SUPERSEDE:
                self.counters[METRIC_SUPERSEDED_COUNT] += 1
                superseded.append(
                    f"{outcome.existing['topic']}: {outcome.existing['statement'][:120]} -> {outcome.record.statement[:120]}"
                )
                self.trace(
                    EVENT_SUPERSEDED,
                    {
                        "old_memory_id": outcome.existing["memory_id"],
                        "new_memory_id": outcome.record.memory_id,
                        "topic": outcome.record.topic,
                    },
                )
            elif outcome.action == ACTION_CONFLICT:
                self.counters[METRIC_CONFLICT_COUNT] += 1
                conflicts.append(outcome.record.memory_id)
                self.trace(
                    EVENT_CONFLICT_DETECTED,
                    {
                        "memory_id": outcome.record.memory_id,
                        "conflict_with": outcome.existing.memory_id
                        if outcome.existing
                        else "",
                        "topic": outcome.record.topic,
                    },
                )
        if promoted or superseded or conflicts or duplicates:
            self._note_mutation()
        return promoted, rejections, superseded, conflicts, duplicates

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def metrics(self):
        counters = dict(self.counters)
        counters[METRIC_EVIDENCE_STORE_SIZE] = self.evidence_store.size()
        counters[METRIC_DURABLE_STORE_SIZE] = self.durable_store.size()
        counters[METRIC_RETRIEVAL_COUNT] = self.retriever.retrieval_count
        counters[METRIC_RETRIEVED_EVIDENCE_COUNT] = self.retriever.retrieved_evidence_count
        counters[METRIC_RETRIEVED_DURABLE_COUNT] = self.retriever.retrieved_durable_count
        counters[METRIC_RETRIEVAL_TOKENS] = getattr(
            self.retriever, "last_total_tokens", 0
        )
        counters[METRIC_INJECTED_TOKENS] = getattr(
            self.retriever, "last_injected_tokens", 0
        )
        return counters

    def _note_mutation(self):
        self.retriever.note_store_generation()
