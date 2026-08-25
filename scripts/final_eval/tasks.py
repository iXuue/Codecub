"""FINAL_FRESH_HOLDOUT_V1 — 10 never-used coding tasks for the CodeCub 2.0
Final Formal Evaluation.

Every task is an objective mutation task with a hidden verifier and a
deterministic repair. Prompts are bug-report style and never leak the file,
symbol, line, patch, or verifier expectation (spec §10).

6 standard + 4 long-horizon (multi-stage discovery -> edit -> verification
failure -> investigation -> second edit -> verification).

These definitions live ONLY under scripts/final_eval/ (evaluation-only) and
are excluded from every model workspace copy.
"""

from __future__ import annotations

from dataclasses import dataclass

# Frozen product SHA (recorded before any formal run; never changes).
PRODUCT_FROZEN_SHA = "b0baa7a9e25de64c1d335ffd72cc9b498b64430e"
GENERATION_ID = "codecub-v2-final-g1"

# Variants (spec §16-§18). WorkingState stays ON whenever the Context
# Compiler is ON; memory is fully OFF for the ablation variants.
V_FULL = "V_FULL"
V_CONTEXT_ONLY = "V_CONTEXT_ONLY"
V_LEGACY_CONTEXT = "V_LEGACY_CONTEXT"

VARIANT_FLAGS = {
    V_FULL: {
        "memory": True,
        "relevant_memory": True,
        "memory_v2": True,
        "evidence_memory": True,
        "durable_memory": True,
        "context_reduction": True,
        "context_compiler": True,
        "prompt_cache": False,
    },
    V_CONTEXT_ONLY: {
        "memory": False,
        "relevant_memory": False,
        "memory_v2": False,
        "evidence_memory": False,
        "durable_memory": False,
        "context_reduction": True,
        "context_compiler": True,
        "prompt_cache": False,
    },
    V_LEGACY_CONTEXT: {
        "memory": False,
        "relevant_memory": False,
        "memory_v2": False,
        "evidence_memory": False,
        "durable_memory": False,
        # context_compiler OFF -> legacy ContextManager with reduction;
        # memory v1 is disabled above, so no accidental legacy retrieval.
        "context_reduction": True,
        "context_compiler": False,
        "prompt_cache": False,
    },
}

STEP_BUDGET = 80
MAX_NEW_TOKENS = 1024
TEMPERATURE = 0.2
TOP_P = 0.9
PROMPT_CACHE = False
PROVIDER = "deepseek"
MODEL = "deepseek-v4-flash"

# Fault scenarios (harness-injected, one-shot, product-agnostic).
FAULT_ONE_SHOT_SHELL = "one_shot_shell_failure"
FAULT_TRANSIENT_READ = "transient_read_error"
FAULT_PATCH_REJECTION = "recoverable_patch_rejection"
FAULT_TRANSIENT_SHELL = "transient_shell_failure"


@dataclass(frozen=True)
class FinalTask:
    task_id: str
    category: str
    prompt: str
    path: str
    baseline: str
    mutation: str
    long_horizon: bool = False
    relevant_paths: tuple[str, ...] = ()  # evaluator-only ground truth
    seed_prompt: str = ""  # predecessor Session A prompt (memory tasks)
    step_budget: int = STEP_BUDGET

    def verifier_code(self):
        return (
            "from pathlib import Path; "
            f"text=Path({self.path!r}).read_text(encoding='utf-8'); "
            f"assert text.count({self.baseline!r}) == 1; "
            f"assert text.count({self.mutation!r}) == 0"
        )


# ----------------------------------------------------------------------
# 6 Standard tasks
# ----------------------------------------------------------------------

_STANDARD = (
    FinalTask(
        task_id="ws_doc_names",
        category="standard",
        prompt=(
            "Project documentation is no longer included in the agent's workspace "
            "context. README files that used to appear in the prompt prefix are "
            "silently missing, so the agent loses project guidance. Restore the "
            "intended project-document scanning behavior."
        ),
        path="codecub/workspace.py",
        baseline='DOC_NAMES = ("AGENTS.md", "README.md", "pyproject.toml", "package.json")',
        mutation='DOC_NAMES = ("AGENTS.md", "pyproject.toml", "package.json")',
        relevant_paths=("codecub/workspace.py",),
        seed_prompt=(
            "Inspect this repository and report: which files does the workspace "
            "context scan for project documentation, and which module builds the "
            "workspace context that appears in the prompt prefix? Do not modify "
            "any files."
        ),
    ),
    FinalTask(
        task_id="ws_git_timeout",
        category="standard",
        prompt=(
            "Workspace context builds now stall for a very long time whenever git "
            "is queried, noticeably slowing every prompt construction and making "
            "the agent feel frozen. Restore the intended bounded git query timeout."
        ),
        path="codecub/workspace.py",
        baseline="GIT_TIMEOUT_SECONDS = 3",
        mutation="GIT_TIMEOUT_SECONDS = 45",
        relevant_paths=("codecub/workspace.py",),
    ),
    FinalTask(
        task_id="tp_research_budget",
        category="standard",
        prompt=(
            "Code-explanation requests now exhaust their investigation budget "
            "almost immediately, forcing the agent to finalize without having "
            "read any source evidence. Restore the intended research budget for "
            "code-explanation tasks."
        ),
        path="codecub/task_policy.py",
        baseline="    return 6 if requires_source_evidence(user_message) else None",
        mutation="    return 2 if requires_source_evidence(user_message) else None",
        relevant_paths=("codecub/task_policy.py",),
    ),
    FinalTask(
        task_id="tp_source_suffixes",
        category="standard",
        prompt=(
            "The runtime no longer recognizes Python files as source files, so "
            "source-evidence requirements are never satisfied for .py targets and "
            "verification loops trigger incorrectly. Restore the source-file "
            "suffix classification."
        ),
        path="codecub/task_policy.py",
        baseline='SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp"}',
        mutation='SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp"}',
        relevant_paths=("codecub/task_policy.py",),
    ),
    FinalTask(
        task_id="tools_list_window",
        category="standard",
        prompt=(
            "Directory listings now hide most files, so the agent can no longer "
            "discover the repository layout when investigating. Restore the "
            "intended directory listing window."
        ),
        path="codecub/tools.py",
        baseline="    for entry in entries[:200]:",
        mutation="    for entry in entries[:50]:",
        relevant_paths=("codecub/tools.py",),
        seed_prompt=(
            "Inspect this repository and report: which module defines the file "
            "listing and read tools, and how does the read tool format file "
            "content for the model? Do not modify any files."
        ),
    ),
    FinalTask(
        task_id="mem_working_file_limit",
        category="standard",
        prompt=(
            "Working memory now retains almost no recently touched files, so the "
            "agent loses track of what it just read across turns and repeats "
            "exploration. Restore the intended recent-file capacity."
        ),
        path="codecub/memory.py",
        baseline="WORKING_FILE_LIMIT = 8",
        mutation="WORKING_FILE_LIMIT = 2",
        relevant_paths=("codecub/memory.py",),
    ),
)

# ----------------------------------------------------------------------
# 4 Long-Horizon tasks (multi-stage; verification loops)
# ----------------------------------------------------------------------

_LONG_HORIZON = (
    FinalTask(
        task_id="ckpt_schema_version",
        category="long-horizon",
        prompt=(
            "Resuming a session after a checkpoint is now always classified as an "
            "incompatible schema, breaking continuation across runs and triggering "
            "the resume regression tests. Restore checkpoint compatibility so "
            "valid checkpoints resume normally while genuinely incompatible ones "
            "are still rejected."
        ),
        path="codecub/runtime.py",
        baseline='CHECKPOINT_SCHEMA_VERSION = "phase1-v1"',
        mutation='CHECKPOINT_SCHEMA_VERSION = "phase1-v0"',
        long_horizon=True,
        relevant_paths=("codecub/runtime.py", "codecub/task_state.py"),
        seed_prompt=(
            "Explain how session resume works in this repository: which runtime "
            "method evaluates the resume state, what fields does a checkpoint "
            "store, and how is a valid vs incompatible checkpoint classified? "
            "Report the file paths you inspected. Do not modify any files."
        ),
    ),
    FinalTask(
        task_id="telemetry_deepseek_usage",
        category="long-horizon",
        prompt=(
            "DeepSeek usage records no longer report actual input tokens for the "
            "chat endpoint, so token accounting and usage snapshots silently lose "
            "data for that provider. Restore the usage accounting while keeping "
            "the mismatch guard behavior."
        ),
        path="codecub/telemetry/parsers/providers.py",
        baseline='            record["context"]["actual_input_tokens"] = prompt',
        mutation='            record["context"]["actual_input_tokens"] = hit',
        long_horizon=True,
        relevant_paths=(
            "codecub/telemetry/parsers/providers.py",
            "codecub/telemetry/aggregation.py",
        ),
        seed_prompt=(
            "Explain how provider usage records are parsed and aggregated in this "
            "repository. Which module maps a deepseek usage payload into the "
            "canonical usage record, and which fields feed token accounting? "
            "Report file paths. Do not modify any files."
        ),
    ),
    FinalTask(
        task_id="watchdog_recovery_window",
        category="long-horizon",
        prompt=(
            "The progress watchdog now declares a stuck agent recovered after "
            "almost no clean progress, so genuinely stuck runs terminate too "
            "easily and healthy runs can be cut short during recovery. Restore "
            "the intended recovery window."
        ),
        path="codecub/watchdog.py",
        baseline="DEFAULT_RECOVERY_WINDOW = 6",
        mutation="DEFAULT_RECOVERY_WINDOW = 2",
        long_horizon=True,
        relevant_paths=("codecub/watchdog.py", "codecub/runtime.py"),
    ),
    FinalTask(
        task_id="edit_decision_range_tracking",
        category="long-horizon",
        prompt=(
            "Repeated overlapping file reads are no longer remembered across "
            "enough ranges, so the edit-decision gate starts misclassifying fresh "
            "reads as repeats and blocking legitimate evidence requests. Restore "
            "the intended read-range tracking capacity."
        ),
        path="codecub/edit_decision.py",
        baseline="MAX_TRACKED_READ_RANGES_PER_PATH = 32",
        mutation="MAX_TRACKED_READ_RANGES_PER_PATH = 4",
        long_horizon=True,
        relevant_paths=("codecub/edit_decision.py", "codecub/runtime.py"),
    ),
)

FINAL_HOLDOUT_V1 = _STANDARD + _LONG_HORIZON

# Tasks that get a Predecessor Session (Session A seed producing legal memory).
# FULL and CONTEXT_ONLY receive the same seed memory snapshot (§24-§25).
MEMORY_SEEDED_TASK_IDS = (
    "ws_doc_names",
    "tools_list_window",
    "ckpt_schema_version",
    "telemetry_deepseek_usage",
)

# Stress mapping: LH task -> fault scenario (V_FULL only, 2 repeats each).
STRESS_PLAN = {
    "ckpt_schema_version": FAULT_ONE_SHOT_SHELL,
    "telemetry_deepseek_usage": FAULT_TRANSIENT_READ,
    "watchdog_recovery_window": FAULT_PATCH_REJECTION,
    "edit_decision_range_tracking": FAULT_TRANSIENT_SHELL,
}

REPEATS = 2


def task_by_id(task_id):
    for task in FINAL_HOLDOUT_V1:
        if task.task_id == task_id:
            return task
    raise KeyError(task_id)
