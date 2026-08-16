"""Memory Extraction — turning task outcomes into MemoryCandidates.

Extraction runs at milestone boundaries (task completed, explicit
remember/save, graceful stuck finalization) — never per tool step. It pulls
from Working State, verification events, successful edits, evidence records
and explicit user instructions, then applies a hard candidate filter.

Rejected outright (spec §26): secrets/tokens/passwords, temporary paths, run
ids, raw tracebacks, line-level transient implementation details, large source
fragments, unsupported guesses, unverified model claims.

Evidence requirement (spec §27): except explicit stable user preferences, a
project fact needs a source reference, a verification event, or runtime
metadata — a bare model guess is never persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import secrets as secretlib

# Candidate types.
CANDIDATE_TEST_COMMAND = "test_command"
CANDIDATE_CONVENTION = "convention"
CANDIDATE_DECISION = "decision"
CANDIDATE_DEPENDENCY = "dependency"
CANDIDATE_PREFERENCE = "preference"
CANDIDATE_ENVIRONMENT = "environment"
CANDIDATE_WORKFLOW = "workflow"
CANDIDATE_PITFALL = "pitfall"

VALID_CANDIDATE_TYPES = frozenset(
    {
        CANDIDATE_TEST_COMMAND,
        CANDIDATE_CONVENTION,
        CANDIDATE_DECISION,
        CANDIDATE_DEPENDENCY,
        CANDIDATE_PREFERENCE,
        CANDIDATE_ENVIRONMENT,
        CANDIDATE_WORKFLOW,
        CANDIDATE_PITFALL,
    }
)

TYPE_TO_TOPIC = {
    CANDIDATE_TEST_COMMAND: "build-and-test",
    CANDIDATE_CONVENTION: "project-conventions",
    CANDIDATE_DECISION: "key-decisions",
    CANDIDATE_DEPENDENCY: "dependency-facts",
    CANDIDATE_PREFERENCE: "user-preferences",
    CANDIDATE_ENVIRONMENT: "environment-constraints",
    CANDIDATE_WORKFLOW: "validated-workflows",
    CANDIDATE_PITFALL: "known-pitfalls",
}

STATEMENT_LIMIT = 300

TEST_COMMAND_PATTERN = re.compile(
    r"(?i)(pytest|python\s+-m\s+pytest|uv\s+run|npm\s+test|yarn\s+test|"
    r"make\s+test|make\s+build|tox\s+-e|pdm\s+run|poetry\s+run|"
    r"python\s+test|nosetests|unittest)"
)

TRANSIENT_PREFIXES = (
    "current goal",
    "current blocker",
    "next step",
    "current phase",
    "key files",
    "freshness",
    "当前目标",
    "当前卡点",
    "下一步",
    "当前阶段",
    "关键文件",
    "已完成",
    "已排除",
    "failed approach",
    "rejected",
    "working state",
)

LINE_LEVEL_PATTERN = re.compile(r"(?i)\b(?:line|行)\s*\d+")

RUN_ID_LIKE_PATTERN = re.compile(r"\b(?:run_|task_|ev_|mem_|ckpt_)[A-Za-z0-9_-]{6,}\b")

TEMPORARY_PATH_PATTERN = re.compile(
    r"(?i)\b(?:temp|tmp|TemporaryDirectory|/tmp/|Temp\\|tempfile)"
)

DURABLE_LINE_PATTERNS = (
    ("project-conventions", re.compile(r"(?i)^Project convention:\s*(.+)$")),
    ("key-decisions", re.compile(r"(?i)^Decision:\s*(.+)$")),
    ("dependency-facts", re.compile(r"(?i)^Dependency:\s*(.+)$")),
    ("user-preferences", re.compile(r"(?i)^Preference:\s*(.+)$")),
    ("build-and-test", re.compile(r"(?i)^Test command:\s*(.+)$")),
    ("build-and-test", re.compile(r"(?i)^Build command:\s*(.+)$")),
    ("environment-constraints", re.compile(r"(?i)^Environment:\s*(.+)$")),
    ("validated-workflows", re.compile(r"(?i)^Workflow:\s*(.+)$")),
    ("known-pitfalls", re.compile(r"(?i)^Pitfall:\s*(.+)$")),
    ("project-conventions", re.compile(r"^项目约定：\s*(.+)$")),
    ("key-decisions", re.compile(r"^决策：\s*(.+)$")),
    ("dependency-facts", re.compile(r"^依赖：\s*(.+)$")),
    ("user-preferences", re.compile(r"^偏好：\s*(.+)$")),
    ("build-and-test", re.compile(r"^测试命令：\s*(.+)$")),
    ("environment-constraints", re.compile(r"^环境：\s*(.+)$")),
    ("validated-workflows", re.compile(r"^工作流：\s*(.+)$")),
    ("known-pitfalls", re.compile(r"^坑：\s*(.+)$")),
)


@dataclass
class MemoryCandidate:
    candidate_type: str
    statement: str
    scope: str = "project"
    source_refs: list = field(default_factory=list)
    confidence: float = 1.0
    reason_to_remember: str = ""
    freshness_dependency: str = ""
    secret_risk: bool = False
    source_user_statement: str = ""
    source_task_id: str = ""
    source_run_id: str = ""
    source_evidence_ids: list = field(default_factory=list)

    @property
    def topic(self):
        return TYPE_TO_TOPIC.get(self.candidate_type, "project-conventions")


def reject_candidate(candidate):
    """Return a rejection reason string, or "" when the candidate is acceptable."""
    statement = str(candidate.statement or "").strip()
    if not statement:
        return "empty"
    if secretlib.contains_secret(statement):
        return "secret"
    lowered = statement.lower()
    if any(lowered.startswith(prefix) for prefix in TRANSIENT_PREFIXES):
        return "transient_task_state"
    if re.search(r"(?i)\b(stdout|stderr|traceback|exit_code|exit code)\b", statement):
        return "noisy_output"
    if LINE_LEVEL_PATTERN.search(statement):
        return "line_level_transient"
    if RUN_ID_LIKE_PATTERN.search(statement):
        return "run_id_or_temp_identity"
    if TEMPORARY_PATH_PATTERN.search(statement):
        return "temporary_path"
    if len(statement) > STATEMENT_LIMIT:
        return "oversized"
    if len(statement) < 8:
        return "too_short"
    # Evidence requirement (spec §27): only explicit user preferences may skip it.
    is_preference = candidate.candidate_type == CANDIDATE_PREFERENCE
    has_evidence = bool(
        candidate.source_refs
        or candidate.source_evidence_ids
        or candidate.source_user_statement
        or candidate.freshness_dependency
    )
    if not is_preference and not has_evidence:
        return "unverified_claim"
    return ""


class MemoryExtractor:
    """Pure extraction; persistence is decided by the consolidator."""

    def __init__(self, task_id="", run_id=""):
        self.task_id = str(task_id or "")
        self.run_id = str(run_id or "")

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    def extract_from_verification(self, working_state):
        """Verified test/build commands → test_command candidates.

        Requires an actual ok verification entry and a test-like command.
        """
        candidates = []
        for entry in (working_state.verification if working_state else []) or []:
            if entry.get("status") != "ok":
                continue
            command = str(entry.get("command") or "").strip()
            if not command or not TEST_COMMAND_PATTERN.search(command):
                continue
            candidates.append(
                MemoryCandidate(
                    candidate_type=CANDIDATE_TEST_COMMAND,
                    statement=f"Test command is: {command[:200]}",
                    source_refs=[
                        {
                            "kind": "verification_event",
                            "command": command,
                            "step": entry.get("step", 0),
                        }
                    ],
                    reason_to_remember="verified passing test/build command",
                    freshness_dependency="none",
                    source_task_id=self.task_id,
                    source_run_id=self.run_id,
                )
            )
        return candidates

    def extract_from_evidence(self, evidence_store, run_id=""):
        """Fresh verification evidence from this run → validated workflow."""
        candidates = []
        for record in evidence_store.latest_records() if evidence_store else []:
            if record.get("kind") != "verification_result":
                continue
            if run_id and record.get("run_id") and record.get("run_id") != run_id:
                continue
            summary = str(record.get("summary") or "").strip()
            if not summary:
                continue
            candidates.append(
                MemoryCandidate(
                    candidate_type=CANDIDATE_WORKFLOW,
                    statement=summary[:240],
                    source_refs=[
                        {
                            "kind": "evidence",
                            "evidence_id": record.get("evidence_id"),
                            "path": record.get("path"),
                        }
                    ],
                    reason_to_remember="verified command recorded as evidence",
                    freshness_dependency="none",
                    source_evidence_ids=[record.get("evidence_id") or ""],
                    source_task_id=self.task_id,
                    source_run_id=run_id or self.run_id,
                )
            )
        return candidates

    def extract_from_user_intent(self, user_message, final_answer):
        """Explicit "remember/save/capture" + labeled final-answer lines.

        A user statement is itself acceptable provenance (spec §27 exception
        for explicit preferences; labeled lines are explicit requests).
        """
        candidates = []
        user_text = str(user_message or "")
        for line in str(final_answer or "").splitlines():
            text = line.strip()
            if not text:
                continue
            for topic_slug, pattern in DURABLE_LINE_PATTERNS:
                match = pattern.match(text)
                if not match:
                    continue
                statement = match.group(1).strip()
                if not statement:
                    break
                candidate_type = {
                    "project-conventions": CANDIDATE_CONVENTION,
                    "key-decisions": CANDIDATE_DECISION,
                    "dependency-facts": CANDIDATE_DEPENDENCY,
                    "user-preferences": CANDIDATE_PREFERENCE,
                    "build-and-test": CANDIDATE_TEST_COMMAND,
                    "environment-constraints": CANDIDATE_ENVIRONMENT,
                    "validated-workflows": CANDIDATE_WORKFLOW,
                    "known-pitfalls": CANDIDATE_PITFALL,
                }.get(topic_slug, CANDIDATE_CONVENTION)
                candidates.append(
                    MemoryCandidate(
                        candidate_type=candidate_type,
                        statement=statement[:STATEMENT_LIMIT],
                        source_refs=[{"kind": "user_final_answer_line"}],
                        source_user_statement=str(user_text)[:300],
                        reason_to_remember="explicit user-labeled durable fact",
                        source_task_id=self.task_id,
                        source_run_id=self.run_id,
                    )
                )
                break
        return candidates

    def extract_all(
        self,
        working_state,
        evidence_store,
        user_message,
        final_answer,
        run_id="",
    ):
        """Run every extraction trigger and return (candidates, rejections)."""
        candidates = []
        candidates.extend(self.extract_from_verification(working_state))
        candidates.extend(self.extract_from_evidence(evidence_store, run_id=run_id))
        candidates.extend(
            self.extract_from_user_intent(user_message, final_answer)
        )
        # Dedupe identical statements.
        seen = set()
        deduped = []
        for candidate in candidates:
            key = (candidate.topic, candidate.statement.strip())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        rejections = []
        kept = []
        for candidate in deduped:
            reason = reject_candidate(candidate)
            if reason:
                rejections.append((candidate.candidate_type, reason, candidate.statement))
            else:
                kept.append(candidate)
        return kept, rejections
