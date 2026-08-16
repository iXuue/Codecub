"""受控 mutation 任务定义。

任务 prompt 描述开发意图而不透露 mutation 的位置或替换文本。每个 mutation
都在 fresh workspace 上施加，并由独立 verifier 判断是否恢复为基线行为。
"""

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class ExperimentTask:
    id: str
    category: str
    prompt: str
    path: str
    baseline: str
    mutation: str
    expected_baseline_occurrences: int = 1
    step_budget: int = 24
    allowed_tools: tuple[str, ...] = ("read_file", "search", "patch_file", "run_shell")
    phases: tuple[str, ...] = ()
    fault: str = ""
    metadata: dict = field(default_factory=dict)
    requires_workspace_change: bool = False

    def verifier_description(self):
        return f"{self.path} contains the baseline implementation fragment"


# 每项 baseline 在当前仓库中应只出现一次；runner 会在真正调用模型前检查，避免
# 因仓库演进而悄悄生成不可靠的 benchmark。
_COMMON = (
    (
        "flag_memory_default",
        "code-understanding",
        "Investigate why normal sessions no longer retain working memory and restore the intended default without changing the public API.",
        "codecub/runtime.py",
        '    "memory": True,',
        '    "memory": False,',
    ),
    (
        "flag_context_default",
        "code-understanding",
        "Prompt construction unexpectedly skips its reduction path by default. Locate the configuration regression and restore the intended behavior.",
        "codecub/runtime.py",
        '    "context_reduction": True,',
        '    "context_reduction": False,',
    ),
    (
        "context_priority",
        "single-module-bug",
        "When prompts overflow, older history is being discarded before lower-priority recall material. Correct the reduction priority and keep the request intact.",
        "codecub/context_manager.py",
        'DEFAULT_REDUCTION_ORDER = ("relevant_memory", "history", "memory", "prefix")',
        'DEFAULT_REDUCTION_ORDER = ("history", "relevant_memory", "memory", "prefix")',
    ),
    (
        "prompt_cache_gate",
        "single-module-bug",
        "A configured prompt-cache opt-out is ignored by the model request path. Fix the feature-flag behavior while preserving supported-provider behavior.",
        "codecub/runtime.py",
        'if self.feature_enabled("prompt_cache") and getattr(self.model_client, "supports_prompt_cache", False):',
        'if getattr(self.model_client, "supports_prompt_cache", False):',
    ),
    (
        "task_state_attempts",
        "cross-file-change",
        "Run records are missing model-attempt accounting after a task state regression. Restore the correct state update and make sure reports remain consistent.",
        "codecub/task_state.py",
        "        self.attempts += 1",
        "        self.attempts += 0",
    ),
    (
        "tool_patch_contract",
        "cross-file-change",
        "Exact patches no longer protect against ambiguous source matches. Repair the tool contract so an ambiguous patch is rejected instead of silently changing code.",
        "codecub/tools.py",
        "    count = text.count(old_text)\n    if count != 1:",
        "    count = text.count(old_text)\n    if count < 1:",
    ),
    (
        "usage_cache_channel",
        "cross-file-change",
        "Usage aggregation is misreporting cache reads. Find the aggregation regression and restore the correct cache accounting channel.",
        "codecub/telemetry/aggregation.py",
        '    cache_read = total(("cache", "read_tokens"))',
        '    cache_read = total(("cache", "write_tokens"))',
    ),
    (
        "workspace_ignore_guard",
        "test-regression",
        "Internal agent state is leaking into ordinary workspace enumeration. Restore the workspace ignore contract and run the relevant regression tests.",
        "codecub/workspace.py",
        'IGNORED_PATH_NAMES = {".git", ".pico", ".codecub", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv"}',
        'IGNORED_PATH_NAMES = {".git", ".pico", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv"}',
    ),
    (
        "resume_identity",
        "test-regression",
        "Resuming after a workspace identity change is classified as valid. Restore the mismatch classification and verify checkpoint recovery behavior.",
        "codecub/runtime.py",
        "                    status = CHECKPOINT_WORKSPACE_MISMATCH_STATUS",
        "                    status = CHECKPOINT_FULL_VALID_STATUS",
    ),
    (
        "repeated_call_guard",
        "recovery",
        "The agent keeps repeating no-progress tool calls one time too long. Restore the intended repeated-call guard without weakening ordinary retries.",
        "codecub/runtime.py",
        "REPEATED_NO_PROGRESS_LIMIT = 5",
        "REPEATED_NO_PROGRESS_LIMIT = 6",
    ),
    (
        "file_summary_limit",
        "memory",
        "File-summary retention was accidentally reduced, causing useful code context to disappear across a task. Restore the intended memory capacity.",
        "codecub/memory.py",
        "FILE_SUMMARY_LIMIT = 6",
        "FILE_SUMMARY_LIMIT = 1",
    ),
    (
        "run_store_usage",
        "cross-file-change",
        "Per-run usage is no longer persisted alongside trace artifacts. Restore the usage artifact path behavior and keep run artifacts isolated.",
        "codecub/run_store.py",
        '        return self.run_dir(run_id) / "usage.jsonl"',
        '        return self.run_dir(run_id) / "usage.json"',
    ),
)

# Development tasks tune the harness and are deliberately excluded from the
# holdout real-agent benchmark.  Their mutations remain reproducible so that
# planning changes can be compared against archived planning baselines.
DEVELOPMENT_TASKS = (
    replace(ExperimentTask(*_COMMON[0]), metadata={"evaluation_role": "development"}, requires_workspace_change=True),
    replace(ExperimentTask(*_COMMON[5]), metadata={"evaluation_role": "development"}, requires_workspace_change=True),
    replace(ExperimentTask(*_COMMON[4]), metadata={"evaluation_role": "development"}, requires_workspace_change=True),
)

REAL_AGENT_TASKS = tuple(ExperimentTask(*row, requires_workspace_change=True) for row in _COMMON[1:])

# 长链路 task 复用现实 mutation，但显式要求先理解多个模块并注入同等历史。
CONTEXT_TASKS = tuple(
    ExperimentTask(
        *row, step_budget=32, phases=("seed_history", "investigate", "repair", "verify"), requires_workspace_change=True
    )
    for row in _COMMON[:8]
)

MEMORY_TASKS = tuple(
    ExperimentTask(
        *row,
        step_budget=32,
        phases=("phase1_architecture", "distractor_work", "phase2_repair", "verify"),
        requires_workspace_change=True,
    )
    for row in (
        _COMMON[0],
        _COMMON[1],
        _COMMON[2],
        _COMMON[4],
        _COMMON[6],
        _COMMON[8],
        _COMMON[9],
        _COMMON[10],
    )
)

RECOVERY_TASKS = (
    replace(
        ExperimentTask(*_COMMON[3]), id="invalid_patch_failure", fault="invalid_patch", requires_workspace_change=True
    ),
    replace(
        ExperimentTask(*_COMMON[0]),
        id="concurrent_file_mutation",
        fault="concurrent_mutation",
        requires_workspace_change=True,
    ),
    replace(
        ExperimentTask(*_COMMON[10]), id="stale_memory_freshness", fault="stale_memory", requires_workspace_change=True
    ),
    replace(
        ExperimentTask(*_COMMON[8]),
        id="workspace_resume_mismatch",
        fault="workspace_mismatch",
        requires_workspace_change=True,
    ),
    replace(ExperimentTask(*_COMMON[7]), id="unsafe_path_access", fault="unsafe_path", requires_workspace_change=True),
)


# Formal holdout tasks are independent from _COMMON and were not used by the
# native-runtime development probes. Prompts disclose behavioral symptoms only.
_FORMAL_HOLDOUT = (
    ("token_nonpositive_guard", "input-validation", "Empty prompt budgets are no longer handled safely. Restore the clipping guard so non-positive limits never return source text.", "codecub/token_budget.py", "    if budget <= 0:", "    if budget < 0:"),
    ("token_reserve_accounting", "configuration-regression", "Configured context windows are allowing generation to consume the reserved safety margin. Correct the prompt-budget calculation.", "codecub/token_budget.py", "    return max(0, int(context_window) - int(max_new_tokens) - int(safety_margin_tokens))", "    return max(0, int(context_window) - int(max_new_tokens))"),
    ("context_window_boolean_rejection", "api-contract", "A boolean provider value is being accepted as a context-window size. Restore strict positive-integer validation.", "codecub/model_capabilities.py", "    if value is None or isinstance(value, bool):", "    if value is None:"),
    ("async_symbol_classification", "code-understanding-repair", "The persisted code index labels asynchronous methods as ordinary functions. Restore the distinct async symbol classification.", "codecub/code_index.py", "        self._function(node, \"async_function\", \"async_method\")", "        self._function(node, \"async_function\", \"method\")"),
    ("symbol_index_storage_location", "persistence-bug", "Symbol-index state is written under an incompatible filename, preventing subsequent reuse. Restore the canonical persisted index location.", "codecub/code_index.py", "        self.path = self.index_dir / \"python_symbols.json\"", "        self.path = self.index_dir / \"symbols.json\""),
    ("usage_private_payload_redaction", "privacy-regression", "Usage snapshots are exposing provider-native diagnostic payloads. Restore the redaction boundary before session persistence.", "codecub/telemetry/contracts.py", "PRIVATE_USAGE_FIELDS = {\"raw_usage\", \"provider_native_metrics\"}", "PRIVATE_USAGE_FIELDS = {\"raw_usage\"}"),
    ("usage_negative_counter_normalization", "metrics-bug", "Malformed provider counters can reduce aggregate usage totals. Restore non-negative normalization before aggregation.", "codecub/telemetry/aggregation.py", "        return max(0, int(value))", "        return int(value)"),
    ("session_usage_identifier_guard", "persistence-bug", "Session usage records with a missing usage identity are being persisted. Restore the guard that keeps usage snapshots deduplicable.", "codecub/usage_store.py", "        if not session_id or not record.get(\"usage_id\"):", "        if not session_id and not record.get(\"usage_id\"):"),
    ("app_cancel_command_contract", "api-contract", "The local-app protocol no longer accepts a valid run cancellation command. Restore the command contract without broadening unknown-command acceptance.", "codecub/app_protocol.py", "    \"cancel_run\",", "    \"cancel\","),
    ("app_event_wire_format", "serialization-regression", "Protocol events are no longer emitted as compact ASCII-safe JSON lines. Restore the wire-format behavior.", "codecub/app_protocol.py", "    return json.dumps(event, ensure_ascii=True, separators=(\",\", \":\")) + \"\\n\"", "    return json.dumps(event) + \"\\n\""),
    ("canonical_path_casefold", "cross-platform-path-bug", "Equivalent workspace paths with different casing are treated as distinct. Restore canonical path normalization for repeat detection.", "codecub/task_policy.py", "    return posixpath.normpath(str(path or \"\").replace(\"\\\\\", \"/\")).casefold()", "    return str(path or \"\").replace(\"\\\\\", \"/\").casefold()"),
    ("atomic_run_artifact_replace", "recovery-adjacent", "Run-artifact persistence can leave stale files after interruption. Restore the atomic replacement operation used for completed writes.", "codecub/run_store.py", "        Path(temp_name).replace(path)", "        Path(temp_name).rename(path)"),
)

REAL_AGENT_TASKS = tuple(ExperimentTask(*row, requires_workspace_change=True) for row in _FORMAL_HOLDOUT)
_ABLATION_HOLDOUT = _FORMAL_HOLDOUT[:8]
CONTEXT_TASKS = tuple(ExperimentTask(*row, step_budget=24, phases=("seed_history", "investigate", "repair", "verify"), requires_workspace_change=True) for row in _ABLATION_HOLDOUT)
MEMORY_TASKS = tuple(ExperimentTask(*row, step_budget=24, phases=("phase1_architecture", "distractor_work", "phase2_repair", "verify"), requires_workspace_change=True) for row in _ABLATION_HOLDOUT)
RECOVERY_TASKS = (
    replace(ExperimentTask(*_FORMAL_HOLDOUT[0]), id="invalid_patch_failure", fault="invalid_patch", requires_workspace_change=True),
    replace(ExperimentTask(*_FORMAL_HOLDOUT[1]), id="concurrent_file_mutation", fault="concurrent_mutation", requires_workspace_change=True),
    replace(ExperimentTask(*_FORMAL_HOLDOUT[4]), id="stale_memory_freshness", fault="stale_memory", requires_workspace_change=True),
    replace(ExperimentTask(*_FORMAL_HOLDOUT[7]), id="workspace_resume_mismatch", fault="workspace_mismatch", requires_workspace_change=True),
    replace(ExperimentTask(*_FORMAL_HOLDOUT[10]), id="unsafe_path_access", fault="unsafe_path", requires_workspace_change=True),
)

def tasks_for_suite(suite):
    suites = {
        "development": DEVELOPMENT_TASKS,
        "real-agent": REAL_AGENT_TASKS,
        "context": CONTEXT_TASKS,
        "memory": MEMORY_TASKS,
        "recovery": RECOVERY_TASKS,
    }
    try:
        return suites[suite]
    except KeyError as exc:
        raise ValueError(f"unknown suite: {suite}") from exc
