import json
from pathlib import Path
from uuid import uuid4

from codecub.experiments.metrics import (
    extract_metrics,
    reduction_percent,
    repeated_reads,
)
from codecub.experiments.runner import ExperimentConfig, ExperimentRunner
from codecub.experiments.tasks import tasks_for_suite


class NonFakeModelClient:
    model = "test-non-fake"


class ScriptedExperimentModelClient(NonFakeModelClient):
    supports_prompt_cache = False

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.last_completion_metadata = {}

    def complete(self, prompt, max_new_tokens, **kwargs):
        del prompt, max_new_tokens, kwargs
        if not self.outputs:
            raise AssertionError("scripted experiment model ran out of outputs")
        return self.outputs.pop(0)


def successful_agent_outputs():
    return [
        '<tool>{"name":"read_file","args":{"path":"codecub/runtime.py","start":1,"end":20}}</tool>',
        '<tool>{"name":"patch_file","args":{"path":"codecub/runtime.py","old_text":"    \\"memory\\": False,","new_text":"    \\"memory\\": True,"}}</tool>',
        "<final>Done.</final>",
    ]


def deterministic_runner(tmp_path):
    source = tmp_path / "source"
    (source / "codecub").mkdir(parents=True)
    (source / "codecub" / "runtime.py").write_text(
        'DEFAULT_FEATURE_FLAGS = {\n    "memory": True,\n}\n', encoding="utf-8"
    )
    runner = ExperimentRunner(
        ExperimentConfig(
            suite="development",
            task_ids=("flag_memory_default",),
            repeat=1,
            output_dir=tmp_path / "experiment-output",
        ),
        repo_root=source,
        model_client_factory=lambda **_kwargs: ScriptedExperimentModelClient(
            successful_agent_outputs()
        ),
    )
    runner.run_root = tmp_path.parent.parent / f"experiment-{tmp_path.name}-{uuid4().hex}"
    runner.runs_path = runner.run_root / "runs.jsonl"
    runner.events_path = runner.run_root / "finalization-events.jsonl"
    return runner


def test_experiment_suites_have_requested_task_counts():
    assert len(tasks_for_suite("real-agent")) == 11
    assert len(tasks_for_suite("development")) == 3
    assert len(tasks_for_suite("context")) == 8
    assert len(tasks_for_suite("memory")) == 8
    assert len(tasks_for_suite("recovery")) >= 5
    assert all(task.requires_workspace_change for task in tasks_for_suite("development"))
    assert all(task.requires_workspace_change for task in tasks_for_suite("real-agent"))


def test_tool_patch_contract_verifier_accepts_only_restored_baseline(tmp_path):
    task = next(task for task in tasks_for_suite("development") if task.id == "tool_patch_contract")
    source = Path(__file__).resolve().parents[1]
    runner = ExperimentRunner(
        ExperimentConfig(suite="development", task_ids=(task.id,), output_dir=tmp_path / "output"),
        repo_root=source,
    )
    workspace = tmp_path / "fixture-copy"
    runner.run_root = tmp_path / "experiment-artifacts"
    runner.copy_workspace(workspace)
    target = workspace / task.path

    preflight = runner.preflight_task(task, workspace)
    assert preflight["baseline_occurrences"] == task.expected_baseline_occurrences
    runner.apply_mutation(task, workspace, preflight=preflight)
    assert runner.verify(task, workspace).returncode != 0
    target.write_text(
        target.read_text(encoding="utf-8").replace(task.mutation, task.baseline, 1),
        encoding="utf-8",
    )
    assert runner.verify(task, workspace).returncode == 0


def test_stale_task_preflight_fails_before_model_client_creation(tmp_path):
    task = next(task for task in tasks_for_suite("development") if task.id == "tool_patch_contract")
    source = tmp_path / "source"
    target = source / task.path
    target.parent.mkdir(parents=True)
    target.write_text(task.baseline + "\n" + task.baseline, encoding="utf-8")
    created = False

    def model_factory(**_kwargs):
        nonlocal created
        created = True
        raise AssertionError("model client must not be created for a stale task")

    runner = ExperimentRunner(
        ExperimentConfig(suite="development", task_ids=(task.id,), output_dir=tmp_path / "output"),
        repo_root=source,
        model_client_factory=model_factory,
    )
    record = runner.run_one(task, 0, "stale-preflight")

    assert created is False
    assert record["status"] == "infrastructure_error"
    assert record["failure_stage"] == "task_preflight"
    assert "expected 1 baseline fragment occurrence(s)" in record["error_message"]


def test_experiment_artifact_paths_round_trip_utf8_with_unicode_and_spaces(tmp_path):
    repo_root = tmp_path / "中文 repo with spaces"
    (repo_root / "codecub").mkdir(parents=True)
    (repo_root / "codecub" / "runtime.py").write_text("x = 1\n", encoding="utf-8")
    experiment_root = tmp_path / "实验 artifacts with spaces"
    runner = ExperimentRunner(
        ExperimentConfig(suite="development", output_dir=experiment_root),
        repo_root=repo_root,
    )
    runner.run_root = experiment_root / "run 中文"
    runner.events_path = runner.run_root / "finalization-events.jsonl"
    workspace = runner.run_root / "workspaces" / "run 中文"
    paths = runner.runtime_artifact_paths(workspace, "run 中文")
    Path(paths["workspace"]).mkdir(parents=True, exist_ok=True)
    for name, path in paths.items():
        if name == "workspace":
            continue
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}", encoding="utf-8")
    debug_path = runner.write_failure_debug("run 中文", "test", RuntimeError("safe"), workspace)
    runner.write_json(
        runner.run_root / "runs.jsonl",
        {"runtime_artifacts": paths, "debug": str(debug_path)},
    )
    record = json.loads((runner.run_root / "runs.jsonl").read_text(encoding="utf-8"))

    assert all(Path(value).exists() for value in record["runtime_artifacts"].values())
    assert Path(record["debug"]).exists()
    assert "中文" in record["runtime_artifacts"]["report"]
    assert "with spaces" in record["runtime_artifacts"]["report"]


def test_variant_flags_keep_non_target_controls_constant(tmp_path):
    context = ExperimentRunner(
        ExperimentConfig(suite="context", variant="off", output_dir=tmp_path)
    )
    memory = ExperimentRunner(
        ExperimentConfig(suite="memory", variant="off", output_dir=tmp_path)
    )
    assert context.feature_flags == {
        "memory": True,
        "relevant_memory": True,
        "context_reduction": False,
        "prompt_cache": False,
    }
    assert memory.feature_flags == {
        "memory": False,
        "relevant_memory": False,
        "context_reduction": True,
        "prompt_cache": False,
    }
    context_on = ExperimentRunner(
        ExperimentConfig(suite="context", variant="on", output_dir=tmp_path)
    )
    memory_on = ExperimentRunner(
        ExperimentConfig(suite="memory", variant="on", output_dir=tmp_path)
    )
    assert context.tasks == context_on.tasks
    assert memory.tasks == memory_on.tasks
    assert {
        key
        for key in context.feature_flags
        if context.feature_flags[key] != context_on.feature_flags[key]
    } == {"context_reduction"}
    assert {
        key
        for key in memory.feature_flags
        if memory.feature_flags[key] != memory_on.feature_flags[key]
    } == {"memory", "relevant_memory"}


def test_repeated_read_detection_ignores_reread_after_write():
    events = [
        {
            "event": "tool_executed",
            "name": "read_file",
            "args": {"path": "a.py", "start": 1, "end": 10},
        },
        {
            "event": "tool_executed",
            "name": "read_file",
            "args": {"path": "./A.py", "start": 2, "end": 10},
        },
        {
            "event": "tool_executed",
            "name": "patch_file",
            "workspace_changed": True,
            "affected_paths": ["A.py"],
            "args": {},
        },
        {
            "event": "tool_executed",
            "name": "read_file",
            "args": {"path": "a.py", "start": 1, "end": 10},
        },
    ]
    assert repeated_reads(events) == (1, 1)
    assert reduction_percent(0, 0) is None


def test_metric_extraction_uses_trace_report_and_usage_fields():
    trace = [
        {
            "event": "tool_executed",
            "name": "read_file",
            "args": {"path": "a.py", "start": 1, "end": 4},
        },
        {
            "event": "tool_executed",
            "name": "read_file",
            "args": {"path": "./A.py", "start": 1, "end": 4},
        },
        {
            "event": "tool_executed",
            "name": "patch_file",
            "args": {},
            "workspace_changed": True,
            "affected_paths": ["a.py"],
        },
        {"event": "checkpoint_created"},
        {
            "event": "prompt_built",
            "prompt_metadata": {
                "prompt_chars": 90,
                "prompt_tokens": 12,
                "budget_reductions": [{"section": "history"}],
                "relevant_memory": {"selected_count": 2},
                "history": {"reused_file_summary_count": 1},
            },
        },
    ]
    report = {
        "attempts": 3,
        "tool_steps": 3,
        "prompt_metadata": {"stale_paths": ["a.py"]},
        "usage_summary": {
            "actual_input_tokens": 100,
            "output_tokens": 30,
            "cache_read_tokens": 40,
        },
        "planning": {
            "productive_exploration_steps": 2,
            "redundant_exploration_steps": 1,
            "rejected_steps": 1,
            "first_action_step": 4,
            "exploration_steps_before_first_action": 3,
            "exploration_warning_count": 1,
            "workspace_change_count": 1,
            "first_workspace_change_step": 4,
            "first_execution_step": 5,
            "first_verification_after_change_step": 5,
            "verification_steps": 2,
            "verification_before_first_action": 1,
            "productive_verification_steps": 1,
            "redundant_verification_steps": 1,
            "implementation_warning_count": 1,
            "avoidable_repeated_read_calls": 1,
            "evidence_evicted_reread_calls": 1,
            "evidence_ledger": [{"path": "a.py"}],
            "evidence_eviction_count": 2,
        },
    }
    metrics = extract_metrics(report, trace)
    assert metrics["tool_steps"] == 3 and metrics["read_calls"] == 2
    assert metrics["repeated_read_calls"] == 1 and metrics["unique_read_files"] == 1
    assert (
        metrics["input_tokens"] == 100
        and metrics["output_tokens"] == 30
        and metrics["cached_tokens"] == 40
    )
    assert (
        metrics["context_reduction_count"] == 1 and metrics["memory_recall_count"] == 2
    )
    assert (
        metrics["file_summary_recall_count"] == 1
        and metrics["stale_memory_rejection_count"] == 1
    )
    assert metrics["first_action_step"] == 4
    assert metrics["productive_exploration_steps"] == 2
    assert metrics["workspace_change_count"] == 1
    assert metrics["first_verification_after_change_step"] == 5
    assert metrics["redundant_verification_steps"] == 1
    assert metrics["avoidable_repeated_read_calls"] == 1
    assert metrics["evidence_evicted_reread_calls"] == 1
    assert metrics["evidence_ledger_entries"] == 1


def test_dry_run_serializes_resume_ready_artifacts(tmp_path):
    result = ExperimentRunner(
        ExperimentConfig(
            suite="development",
            task_ids=("flag_memory_default",),
            repeat=1,
            dry_run=True,
            output_dir=tmp_path,
        )
    ).run()
    root = result["root"]
    assert {"manifest.json", "runs.jsonl", "summary.json", "report.md"} <= {
        item.name for item in root.iterdir()
    }
    row = json.loads((root / "runs.jsonl").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert "git_dirty_state" in manifest
    assert manifest["git_dirty_state"]["dirty"] in {True, False, None}
    assert (
        row["status"] == "not_run" and row["passed"] is None
        if "passed" in row
        else row["status"] == "not_run"
    )


def test_workspace_copy_excludes_outputs_and_generated_dependencies(tmp_path):
    source = tmp_path / "source"
    (source / "codecub").mkdir(parents=True)
    (source / "codecub" / "runtime.py").write_text("runtime", encoding="utf-8")
    for directory in (
        "artifacts",
        ".codecub",
        ".git",
        "desktop/node_modules",
        "desktop/.electron-cache",
    ):
        path = source / directory
        path.mkdir(parents=True)
        (path / "ignored.txt").write_text("ignored", encoding="utf-8")
    (source / ".env").write_text("DEEPSEEK_API_KEY=secret", encoding="utf-8")

    runner = ExperimentRunner(
        ExperimentConfig(
            suite="real-agent", output_dir=source / "artifacts" / "experiments"
        ),
        repo_root=source,
    )
    runner.run_root.mkdir(parents=True)
    workspace = runner.run_root / "workspaces" / "one"
    runner.copy_workspace(workspace)

    assert (workspace / "codecub" / "runtime.py").exists()
    for directory in (
        "artifacts",
        ".codecub",
        ".git",
        "desktop/node_modules",
        "desktop/.electron-cache",
    ):
        assert not (workspace / directory).exists()
    assert not (workspace / ".env").exists()


def test_runtime_artifacts_stay_inside_fresh_workspace(tmp_path):
    source = tmp_path / "source"
    (source / "codecub").mkdir(parents=True)
    (source / "codecub" / "runtime.py").write_text("runtime", encoding="utf-8")
    runner = ExperimentRunner(
        ExperimentConfig(suite="real-agent", output_dir=tmp_path / "output"),
        repo_root=source,
        model_client_factory=lambda **_kwargs: NonFakeModelClient(),
    )
    runner.run_root.mkdir(parents=True)
    workspace = runner.run_root / "workspaces" / "one"
    runner.copy_workspace(workspace)

    agent = runner.make_agent(workspace, runner.tasks[0])

    assert Path(agent.workspace.repo_root) == workspace.resolve()
    assert agent.session_store.root == workspace / ".codecub" / "sessions"
    assert agent.run_store.root == workspace / ".codecub" / "runs"


def test_pytest_configuration_does_not_collect_experiment_artifacts():
    project_config = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'testpaths = ["tests"]' in project_config
    assert 'norecursedirs = ["artifacts"]' in project_config


def test_runtime_artifacts_finalize_inside_fresh_workspace(tmp_path):
    runner = deterministic_runner(tmp_path)
    runner.write_report = lambda _rows, _summary: None

    result = runner.run()
    row = result["rows"][0]
    runtime = row["runtime_artifacts"]

    assert Path(runtime["workspace"]).is_relative_to(result["root"] / "workspaces")
    assert Path(runtime["report"]).exists()
    assert (result["root"] / "runs.jsonl").exists()
    assert (result["root"] / "summary.json").exists()
    assert row["input_tokens"] is None
    events = [
        json.loads(line)["event"]
        for line in (result["root"] / "finalization-events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert events == [
        "agent_finished",
        "runtime_report_written",
        "verifier_started",
        "verifier_finished",
        "metrics_started",
        "metrics_finished",
        "run_record_written",
        "summary_written",
        "experiment_finished",
    ]


def test_verifier_failure_still_writes_one_experiment_record(tmp_path):
    runner = deterministic_runner(tmp_path)
    runner.write_report = lambda _rows, _summary: None
    runner.verify = lambda *_args: __import__("subprocess").CompletedProcess(
        [], 1, stdout="", stderr=""
    )

    result = runner.run()
    rows = [
        json.loads(line)
        for line in (result["root"] / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert len(rows) == 1
    assert rows[0]["status"] == "fail"
    assert rows[0]["verifier_passed"] is False
    assert runner.append_row(rows[0]) is False


def test_metrics_failure_writes_redacted_infrastructure_record(tmp_path, monkeypatch):
    runner = deterministic_runner(tmp_path)
    runner.write_report = lambda _rows, _summary: None
    secret = "deepseek-secret-value"

    def fail_metrics(*_args):
        raise RuntimeError(f"DEEPSEEK_API_KEY={secret} Authorization: Bearer {secret}")

    monkeypatch.setattr("codecub.experiments.runner.extract_metrics", fail_metrics)
    result = runner.run()
    row = result["rows"][0]
    debug = Path(row["experiment_artifacts"]["debug"])
    debug_text = debug.read_text(encoding="utf-8")

    assert row["status"] == "infrastructure_error"
    assert row["failure_stage"] == "metrics_collection"
    assert row["error_type"] == "RuntimeError"
    assert Path(row["runtime_artifacts"]["workspace"]).name.endswith(
        "flag_memory_default-0"
    )
    assert secret not in row["error_message"]
    assert secret not in debug_text
    assert "<redacted>" in debug_text
    events = (result["root"] / "finalization-events.jsonl").read_text(encoding="utf-8")
    assert '"event": "finalization_failed"' in events
