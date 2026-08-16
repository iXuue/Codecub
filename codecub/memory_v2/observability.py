"""Memory 2.0 observability contract: trace event names and metric keys.

Kept in one place so runtime trace emission and experiment metrics extraction
always use the same constants (no string drift between codecub and experiments).
"""

# Trace events (emitted through runtime.emit_trace with payload dicts).
EVENT_CANDIDATE_EXTRACTED = "memory_candidate_extracted"
EVENT_CANDIDATE_REJECTED = "memory_candidate_rejected"
EVENT_CANDIDATE_CONSOLIDATED = "memory_candidate_consolidated"
EVENT_WRITTEN = "memory_written"
EVENT_SUPERSEDED = "memory_superseded"
EVENT_CONFLICT_DETECTED = "memory_conflict_detected"
EVENT_RETRIEVAL_STARTED = "memory_retrieval_started"
EVENT_RETRIEVAL_FINISHED = "memory_retrieval_finished"
EVENT_EVIDENCE_STALE = "memory_evidence_stale"
EVENT_EVIDENCE_REVALIDATED = "memory_evidence_revalidated"
EVENT_MIGRATION_STARTED = "memory_migration_started"
EVENT_MIGRATION_FINISHED = "memory_migration_finished"

MEMORY_EVENTS = frozenset(
    {
        EVENT_CANDIDATE_EXTRACTED,
        EVENT_CANDIDATE_REJECTED,
        EVENT_CANDIDATE_CONSOLIDATED,
        EVENT_WRITTEN,
        EVENT_SUPERSEDED,
        EVENT_CONFLICT_DETECTED,
        EVENT_RETRIEVAL_STARTED,
        EVENT_RETRIEVAL_FINISHED,
        EVENT_EVIDENCE_STALE,
        EVENT_EVIDENCE_REVALIDATED,
        EVENT_MIGRATION_STARTED,
        EVENT_MIGRATION_FINISHED,
    }
)

# Report-level metrics (report["memory_v2"] = {...}).
METRIC_CANDIDATE_COUNT = "candidate_count"
METRIC_CANDIDATE_REJECTED_COUNT = "candidate_rejected_count"
METRIC_CANDIDATE_PROMOTED_COUNT = "candidate_promoted_count"
METRIC_DUPLICATE_COUNT = "duplicate_count"
METRIC_SUPERSEDED_COUNT = "superseded_count"
METRIC_CONFLICT_COUNT = "conflict_count"
METRIC_EVIDENCE_STORE_SIZE = "evidence_store_size"
METRIC_DURABLE_STORE_SIZE = "durable_store_size"
METRIC_RETRIEVAL_COUNT = "retrieval_count"
METRIC_RETRIEVED_EVIDENCE_COUNT = "retrieved_evidence_count"
METRIC_RETRIEVED_DURABLE_COUNT = "retrieved_durable_count"
METRIC_STALE_EVIDENCE_COUNT = "stale_evidence_count"
METRIC_REVALIDATED_EVIDENCE_COUNT = "revalidated_evidence_count"
METRIC_RETRIEVAL_TOKENS = "retrieval_tokens"
METRIC_INJECTED_TOKENS = "injected_tokens"
# Retrieval quality metrics (dev tests can compute; real runs descriptive).
METRIC_STALE_USED_WITHOUT_REVALIDATION = "stale_used_without_revalidation"
METRIC_MEMORY_GUIDED_REREAD_COUNT = "memory_guided_reread_count"
METRIC_IRRELEVANT_RETRIEVAL_COUNT = "irrelevant_retrieval_count"
METRIC_DUPLICATE_RETRIEVAL_COUNT = "duplicate_retrieval_count"

MEMORY_METRICS = frozenset(
    {
        METRIC_CANDIDATE_COUNT,
        METRIC_CANDIDATE_REJECTED_COUNT,
        METRIC_CANDIDATE_PROMOTED_COUNT,
        METRIC_DUPLICATE_COUNT,
        METRIC_SUPERSEDED_COUNT,
        METRIC_CONFLICT_COUNT,
        METRIC_EVIDENCE_STORE_SIZE,
        METRIC_DURABLE_STORE_SIZE,
        METRIC_RETRIEVAL_COUNT,
        METRIC_RETRIEVED_EVIDENCE_COUNT,
        METRIC_RETRIEVED_DURABLE_COUNT,
        METRIC_STALE_EVIDENCE_COUNT,
        METRIC_REVALIDATED_EVIDENCE_COUNT,
        METRIC_RETRIEVAL_TOKENS,
        METRIC_INJECTED_TOKENS,
        METRIC_STALE_USED_WITHOUT_REVALIDATION,
        METRIC_MEMORY_GUIDED_REREAD_COUNT,
        METRIC_IRRELEVANT_RETRIEVAL_COUNT,
        METRIC_DUPLICATE_RETRIEVAL_COUNT,
    }
)

# Context Compiler metadata keys (added to compile metadata under "memory_layer").
META_MEMORY_TOKENS = "memory_tokens"
META_MEMORY_EVIDENCE_COUNT = "memory_evidence_count"
META_MEMORY_DURABLE_COUNT = "memory_durable_count"
META_MEMORY_STALE_COUNT = "memory_stale_count"
