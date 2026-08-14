import json

from codecub.experiments.metrics import reduction_percent, repeated_reads
from codecub.experiments.runner import ExperimentConfig, ExperimentRunner
from codecub.experiments.tasks import tasks_for_suite


def test_experiment_suites_have_requested_task_counts():
    assert len(tasks_for_suite("real-agent")) == 12
    assert len(tasks_for_suite("context")) == 8
    assert len(tasks_for_suite("memory")) == 8
    assert len(tasks_for_suite("recovery")) >= 5


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
            "args": {"path": "a.py", "start": 5, "end": 12},
        },
        {
            "event": "tool_executed",
            "name": "patch_file",
            "workspace_changed": True,
            "affected_paths": ["a.py"],
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


def test_dry_run_serializes_resume_ready_artifacts(tmp_path):
    result = ExperimentRunner(
        ExperimentConfig(
            suite="real-agent",
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
    assert (
        row["status"] == "not_run" and row["passed"] is None
        if "passed" in row
        else row["status"] == "not_run"
    )
