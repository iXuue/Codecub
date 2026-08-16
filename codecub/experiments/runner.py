"""统一的真实模型实验 runner。"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..cli import (
    _build_model_client,
    _configured_secret_names,
    build_arg_parser,
    load_env_file,
)
from ..evaluator import run_harness_regression_v2
from ..models import FakeModelClient
from ..run_store import RunStore
from ..runtime import Pico, SessionStore
from ..workspace import WorkspaceContext
from .metrics import extract_metrics, summarize
from .tasks import tasks_for_suite


_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?im)^(\s*(?:[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD)|DEEPSEEK_API_KEY)\s*=\s*).*$"),
    re.compile(r"(?i)((?:[A-Z][A-Z0-9_]*?(?:API_KEY|KEY|SECRET|TOKEN|PASSWORD))\s*=\s*)[^\s,'\"]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s'\"]+"),
    re.compile(r"(?i)(\bbearer\s+)[^\s'\"]+"),
    re.compile(r"(?i)(\b(?:api[ _-]?key|secret|password|token)\b\s*[:=]\s*)[^\s,'\"]+"),
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def git_commit(root):
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


def git_dirty_state(root):
    try:
        output = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.splitlines()
    except Exception:
        return {"dirty": None, "paths": []}
    return {"dirty": bool(output), "paths": [line[3:] for line in output if len(line) > 3]}


@dataclass
class ExperimentConfig:
    suite: str
    variant: str = "default"
    repeat: int = 3
    task_ids: tuple[str, ...] = ()
    provider: str | None = None
    model: str | None = None
    output_dir: Path = Path("artifacts/experiments")
    max_steps: int | None = None
    max_new_tokens: int = 1024
    approval: str = "auto"
    context_window: int | None = None
    temperature: float = 0.2
    top_p: float = 0.9
    resume: bool = False
    dry_run: bool = False


class ExperimentRunner:
    """复用 CLI 的真实 provider 装配与 Pico runtime，绝不退化为 FakeModelClient。"""

    def __init__(self, config, repo_root=None, model_client_factory=None):
        self.config = config
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.model_client_factory = model_client_factory
        self.tasks = self._select_tasks()
        self.feature_flags = self._feature_flags()
        self.run_root = self._new_or_resume_root()
        self.runs_path = self.run_root / "runs.jsonl"
        self.events_path = self.run_root / "finalization-events.jsonl"

    def _select_tasks(self):
        tasks = tasks_for_suite(self.config.suite)
        if self.config.task_ids:
            wanted = set(self.config.task_ids)
            tasks = tuple(task for task in tasks if task.id in wanted)
            missing = wanted - {task.id for task in tasks}
            if missing:
                raise ValueError(
                    f"unknown task ids for {self.config.suite}: {', '.join(sorted(missing))}"
                )
        return tasks

    def _feature_flags(self):
        flags = {
            "memory": True,
            "relevant_memory": True,
            "context_reduction": True,
            "prompt_cache": False,
        }
        if self.config.suite == "context":
            flags["context_reduction"] = self.config.variant == "on"
        if self.config.suite == "memory":
            enabled = self.config.variant == "on"
            flags.update({"memory": enabled, "relevant_memory": enabled})
        return flags

    def _new_or_resume_root(self):
        base = Path(self.config.output_dir).resolve()
        if self.config.resume:
            candidates = sorted(
                base.glob(f"{self.config.suite}-*"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                return candidates[0]
        identifier = f"{self.config.suite}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        return base / identifier

    def planned_run_id(self, task, repeat_index):
        return f"{self.config.suite}-{self.config.variant}-{task.id}-{repeat_index}"

    def existing_ids(self):
        if not self.runs_path.exists():
            return set()
        return {
            json.loads(line).get("run_id")
            for line in self.runs_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    def run(self):
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.write_manifest()
        existing = self.existing_ids() if self.config.resume else set()
        rows = self.load_rows() if self.config.resume else []
        for task in self.tasks:
            for repeat_index in range(self.config.repeat):
                run_id = self.planned_run_id(task, repeat_index)
                if run_id in existing:
                    continue
                row = self.run_one(task, repeat_index, run_id)
                if self.append_row(row):
                    self.emit_finalization_event("run_record_written", run_id)
                rows.append(row)
        summary = summarize(rows)
        summary["suite"] = self.config.suite
        summary["variant"] = self.config.variant
        self.write_json(self.run_root / "summary.json", summary)
        self.emit_finalization_event("summary_written")
        self.write_report(rows, summary)
        self.emit_finalization_event("experiment_finished")
        return {"root": self.run_root, "rows": rows, "summary": summary}

    def write_manifest(self):
        payload = {
            "schema_version": 1,
            "experiment_run_id": self.run_root.name,
            "created_at": utc_now(),
            "git_commit": git_commit(self.repo_root),
            "git_dirty_state": git_dirty_state(self.repo_root),
            "suite": self.config.suite,
            "variant": self.config.variant,
            "repeat": self.config.repeat,
            "task_ids": [task.id for task in self.tasks],
            "provider": self.config.provider,
            "model": self.config.model,
            "feature_flags": self.feature_flags,
            "max_steps": self.config.max_steps,
            "max_new_tokens": self.config.max_new_tokens,
            "approval": self.config.approval,
            "context_window": self.config.context_window,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "prompt_cache": False,
            "tasks": [asdict(task) for task in self.tasks],
        }
        self.write_json(self.run_root / "manifest.json", payload)

    def run_one(self, task, repeat_index, run_id):
        started = utc_now()
        started_clock = time.monotonic()
        workspace = self.run_root / "workspaces" / run_id
        record = self._base_record(task, repeat_index, run_id, started, workspace)
        failure_stage = "workspace_setup"
        try:
            self.copy_workspace(workspace)
            failure_stage = "task_preflight"
            preflight = self.preflight_task(task, workspace)
            failure_stage = "mutation"
            self.apply_mutation(task, workspace, preflight=preflight)
            if self.config.dry_run:
                record.update(
                    {
                        "status": "not_run",
                        "failure_category": "dry_run",
                        "finished_at": utc_now(),
                        "duration_ms": int((time.monotonic() - started_clock) * 1000),
                    }
                )
                return record
            failure_stage = "agent_setup"
            agent = self.make_agent(workspace, task)
            self.seed_history(agent, task)
            self.inject_fault(agent, task, workspace)
            failure_stage = "agent_execution"
            agent.ask(task.prompt, run_id=run_id)
            self.emit_finalization_event("agent_finished", run_id)
            failure_stage = "runtime_report_collection"
            report = agent.run_store.load_report(run_id)
            record["runtime_artifacts"] = self.runtime_artifact_paths(workspace, run_id)
            self.emit_finalization_event("runtime_report_written", run_id)
            trace_path = agent.run_store.trace_path(run_id)
            trace = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            failure_stage = "verifier"
            self.emit_finalization_event("verifier_started", run_id)
            verifier = self.verify(task, workspace)
            self.emit_finalization_event(
                "verifier_finished", run_id, exit_code=verifier.returncode
            )
            failure_stage = "metrics_collection"
            self.emit_finalization_event("metrics_started", run_id)
            metrics = extract_metrics(report, trace)
            self.emit_finalization_event("metrics_finished", run_id)
            within = report.get("tool_steps", 0) <= (
                self.config.max_steps or task.step_budget
            )
            verifier_passed = verifier.returncode == 0
            completed = report.get("stop_reason") == "final_answer_returned"
            passed = bool(within and verifier_passed and completed)
            record.update(metrics)
            record.update(
                {
                    "status": "pass" if passed else "fail",
                    "passed": passed,
                    "verifier_passed": verifier_passed,
                    "within_budget": within,
                    "failure_category": None
                    if passed
                    else self.failure_category(within, verifier_passed, completed),
                    "stop_reason": report.get("stop_reason"),
                    "finished_at": utc_now(),
                    "duration_ms": int((time.monotonic() - started_clock) * 1000),
                    "runtime_artifacts": self.runtime_artifact_paths(workspace, run_id),
                    "verifier_exit_code": verifier.returncode,
                    "verifier_stdout": self.redact_sensitive(verifier.stdout[-1000:]),
                    "verifier_stderr": self.redact_sensitive(verifier.stderr[-1000:]),
                }
            )
            self.add_recovery_metrics(record, trace, task)
        except Exception as exc:
            debug_path = self.write_failure_debug(
                run_id, failure_stage, exc, workspace
            )
            record.update(
                {
                    "status": "infrastructure_error",
                    "passed": False,
                    "verifier_passed": False,
                    "within_budget": False,
                    "failure_category": "infrastructure_error",
                    "failure_stage": failure_stage,
                    "error_type": type(exc).__name__,
                    "error_message": self.redact_sensitive(str(exc))[:500],
                    "runtime_artifacts": self.runtime_artifact_paths(workspace, run_id),
                    "experiment_artifacts": {
                        "root": str(self.run_root),
                        "events": str(self.events_path),
                        "debug": str(debug_path),
                    },
                    "finished_at": utc_now(),
                    "duration_ms": int((time.monotonic() - started_clock) * 1000),
                }
            )
            self.emit_finalization_event(
                "finalization_failed",
                run_id,
                failure_stage=failure_stage,
                error_type=type(exc).__name__,
            )
        return record

    def runtime_artifact_paths(self, workspace, run_id):
        run_root = Path(workspace) / ".codecub" / "runs" / run_id
        return {
            "workspace": str(workspace),
            "task_state": str(run_root / "task_state.json"),
            "trace": str(run_root / "trace.jsonl"),
            "report": str(run_root / "report.json"),
            "usage": str(run_root / "usage.jsonl"),
        }

    def redact_sensitive(self, value):
        text = str(value)
        for name, secret in os.environ.items():
            if name.upper().endswith(("KEY", "SECRET", "TOKEN", "PASSWORD")) and secret:
                text = text.replace(secret, "<redacted>")
        for pattern in _SENSITIVE_VALUE_PATTERNS:
            text = pattern.sub(r"\1<redacted>", text)
        return text

    def emit_finalization_event(self, event, run_id=None, **details):
        self.run_root.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": utc_now(), "event": event}
        if run_id is not None:
            payload["run_id"] = run_id
        if details:
            payload["details"] = {
                key: self.redact_sensitive(value) if isinstance(value, str) else value
                for key, value in details.items()
            }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def write_failure_debug(self, run_id, failure_stage, exc, workspace):
        debug_path = self.run_root / "debug" / f"{run_id}.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_json(
            debug_path,
            {
                "run_id": run_id,
                "failure_stage": failure_stage,
                "error_type": type(exc).__name__,
                "error_message": self.redact_sensitive(str(exc)),
                "traceback": self.redact_sensitive(
                    "".join(traceback.format_exception(exc))
                ),
                "runtime_artifacts": self.runtime_artifact_paths(workspace, run_id),
            },
        )
        return debug_path

    def copy_workspace(self, destination):
        # artifacts 是 runner 输出，不是任务 fixture。node_modules、缓存、运行
        # 状态同样不能成为 agent 的输入；否则既会污染 benchmark，也会令复制量
        # 随实验次数增长。这里按目录名在任意深度排除它们。
        ignored_names = {
            ".codecub",
            ".env",
            ".env.local",
            ".env.development",
            ".env.production",
            ".electron-cache",
            ".git",
            ".next",
            ".pytest_cache",
            ".ruff_cache",
            ".tmp",
            ".uv-cache",
            ".venv",
            "__pycache__",
            "artifacts",
            "build",
            "dist",
            "dist-electron",
            "dist-renderer",
            "node_modules",
            "release",
        }

        def ignore(_directory, names):
            return {
                name for name in names if name in ignored_names or name.endswith(".pyc")
            }

        # staging 预先放在实验输出根目录。由于 `artifacts` 从源树排除，源树扫描
        # 不会再看见该 staging；随后同卷 move 取代第二次 copytree，避免原先的
        # repo -> temp -> workspace 双倍复制，以及 destination 位于 source 内时的
        # 递归风险。
        destination = Path(destination)
        staging = self.run_root / f".workspace-staging-{uuid.uuid4().hex}"
        shutil.copytree(self.repo_root, staging, ignore=ignore)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(destination))
        forbidden = [name for name in ignored_names if (destination / name).exists()]
        if forbidden:
            raise RuntimeError(
                f"workspace copy included ignored paths: {', '.join(sorted(forbidden))}"
            )

    def preflight_task(self, task, workspace):
        path = workspace / task.path
        if not path.is_file():
            raise ValueError(f"task {task.id} is stale: fixture path is not a file: {task.path}")
        text = path.read_text(encoding="utf-8")
        count = text.count(task.baseline)
        expected = task.expected_baseline_occurrences
        if count != expected:
            raise ValueError(
                f"task {task.id} is stale: expected {expected} baseline fragment occurrence(s) in {task.path}, found {count}"
            )
        mutation_count = text.count(task.mutation)
        if mutation_count:
            raise ValueError(
                f"task {task.id} is stale: fixture already contains mutation fragment {mutation_count} time(s)"
            )
        return {
            "path": path,
            "text": text,
            "baseline_occurrences": count,
            "mutation_occurrences": mutation_count,
        }

    def apply_mutation(self, task, workspace, *, preflight=None):
        preflight = preflight or self.preflight_task(task, workspace)
        path = preflight["path"]
        text = preflight["text"]
        path.write_text(text.replace(task.baseline, task.mutation, 1), encoding="utf-8")

    def make_agent(self, workspace, task):
        argv = [
            "--cwd",
            str(workspace),
            "--approval",
            self.config.approval,
            "--max-steps",
            str(self.config.max_steps or task.step_budget),
            "--max-new-tokens",
            str(self.config.max_new_tokens),
            "--temperature",
            str(self.config.temperature),
            "--top-p",
            str(self.config.top_p),
        ]
        if self.config.provider:
            argv += ["--provider", self.config.provider]
        if self.config.model:
            argv += ["--model", self.config.model]
        args = build_arg_parser().parse_args(argv)
        # The fixture has no .git by design.  Build the workspace context with an
        # explicit root so that ancestor discovery cannot attach this run to the
        # source repository and write runtime artifacts into its .codecub tree.
        workspace_context = WorkspaceContext.build(workspace, repo_root_override=workspace)
        # Credentials are process configuration, never fixture inputs.  Load only
        # the source repository's dotenv file after the fixture copy has excluded it.
        load_env_file(self.repo_root)
        model_client = (
            self.model_client_factory(task=task, workspace=workspace)
            if self.model_client_factory is not None
            else _build_model_client(args)
        )
        if isinstance(model_client, FakeModelClient):
            raise RuntimeError("real experiments reject FakeModelClient")
        agent = Pico(
            model_client=model_client,
            workspace=workspace_context,
            session_store=SessionStore(workspace / ".codecub" / "sessions"),
            run_store=RunStore(workspace / ".codecub" / "runs"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
            secret_env_names=_configured_secret_names(args),
            feature_flags=self.feature_flags,
            context_window=self.config.context_window,
            allowed_tools=task.allowed_tools,
            requires_workspace_change=task.requires_workspace_change,
        )
        return agent

    def seed_history(self, agent, task):
        if self.config.suite not in {"context", "memory", "recovery"}:
            return
        for index in range(8):
            agent.record(
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": f"Prior repository investigation {index}: inspect runtime, context manager, memory and tool boundaries for task {task.id}.",
                    "created_at": utc_now(),
                }
            )
        if self.config.suite in {"memory", "recovery"}:
            agent.memory.set_file_summary(
                task.path,
                f"The baseline contract in {task.path} is important for {task.category}.",
            )
            agent.memory.remember_file(task.path)
            agent.session["memory"] = agent.memory.to_dict()
            agent.session_store.save(agent.session)

    def inject_fault(self, agent, task, workspace):
        if not task.fault:
            return
        original = agent.run_tool
        fired = {"value": False}

        def wrapped(name, args):
            if not fired["value"] and name == "patch_file":
                fired["value"] = True
                if task.fault == "invalid_patch":
                    agent._last_tool_result_metadata = {
                        "tool_status": "rejected",
                        "tool_error_code": "injected_invalid_patch",
                        "security_event_type": "",
                        "affected_paths": [],
                        "workspace_changed": False,
                    }
                    return "error: injected patch failure; re-read the file and correct the patch"
                if task.fault == "concurrent_mutation":
                    target = workspace / task.path
                    target.write_text(
                        target.read_text(encoding="utf-8")
                        + "\n# concurrent experiment update\n",
                        encoding="utf-8",
                    )
                if task.fault == "stale_memory":
                    target = workspace / task.path
                    target.write_text(
                        target.read_text(encoding="utf-8") + "\n# freshness changed\n",
                        encoding="utf-8",
                    )
            return original(name, args)

        agent.run_tool = wrapped
        if task.fault == "workspace_mismatch":
            agent.session["runtime_identity"] = {
                "workspace_fingerprint": "experiment-mismatch"
            }
        if task.fault == "unsafe_path":
            agent.record(
                {
                    "role": "assistant",
                    "content": "Recovery constraint: do not access ../outside; use workspace-local files.",
                    "created_at": utc_now(),
                }
            )

    def verify(self, task, workspace):
        code = f"from pathlib import Path; text=Path({task.path!r}).read_text(encoding='utf-8'); assert text.count({task.baseline!r}) == 1; assert text.count({task.mutation!r}) == 0"
        return subprocess.run(
            [sys.executable, "-c", code], cwd=workspace, capture_output=True, text=True
        )

    def _base_record(self, task, repeat_index, run_id, started, workspace):
        return {
            "schema_version": 1,
            "experiment": self.config.suite,
            "variant": self.config.variant,
            "task_id": task.id,
            "task_category": task.category,
            "repeat_index": repeat_index,
            "provider": self.config.provider,
            "model": self.config.model,
            "git_commit": git_commit(self.repo_root),
            "run_id": run_id,
            "started_at": started,
            "allowed_tools": list(task.allowed_tools),
            "requires_workspace_change": task.requires_workspace_change,
            "step_budget": self.config.max_steps or task.step_budget,
            "runtime_artifacts": self.runtime_artifact_paths(workspace, run_id),
            "experiment_artifacts": {
                "root": str(self.run_root),
                "events": str(self.events_path),
            },
        }

    @staticmethod
    def failure_category(within, verifier, completed):
        if not within:
            return "budget_exceeded"
        if not verifier:
            return "verifier_failed"
        if not completed:
            return "runtime_stop_reason"
        return "unknown"

    def add_recovery_metrics(self, record, trace, task):
        if not task.fault:
            return
        tool_events = [
            event for event in trace if event.get("event") == "tool_executed"
        ]
        unsafe = [
            event
            for event in tool_events
            if event.get("security_event_type") in {"path_escape", "read_only_block"}
        ]
        record.update(
            {
                "recovery_triggered": True,
                "recovery_success": bool(record.get("passed")),
                "recovery_steps": len(tool_events),
                "unsafe_operation_attempts": len(unsafe),
                "unsafe_operation_blocked": sum(
                    event.get("tool_status") == "rejected" for event in unsafe
                ),
            }
        )

    def append_row(self, row):
        if row.get("run_id") in self.existing_ids():
            return False
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return True

    def load_rows(self):
        return (
            [
                json.loads(line)
                for line in self.runs_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if self.runs_path.exists()
            else []
        )

    @staticmethod
    def write_json(path, value):
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_report(self, rows, summary):
        harness = run_harness_regression_v2(
            artifact_path=self.run_root / "harness-regression.json"
        )
        lines = [
            "# CodeCub Agent Experiment Report",
            "",
            "## Environment",
            "",
            f"- Git Commit: {git_commit(self.repo_root) or 'unknown'}",
            f"- Provider: {self.config.provider or 'environment default'}",
            f"- Model: {self.config.model or 'environment default'}",
            f"- Date: {utc_now()}",
            f"- Experiment Config: suite={self.config.suite}, variant={self.config.variant}, repeat={self.config.repeat}",
            "",
            "## Harness Regression",
            "",
            f"- Passed: {harness['summary']['passed']}/{harness['summary']['total_tasks']}",
            "",
            f"## Experiment: {self.config.suite}",
            "",
            "| Task | Repeat | Result | Verifier | Tool steps | First action | Redundant exploration | Warnings | Input tokens | Failure |",
            "|---|---:|---|---|---:|---:|---:|---:|---:|---|",
        ]
        for row in rows:
            lines.append(
                f"| {row['task_id']} | {row['repeat_index']} | {row.get('status')} | {row.get('verifier_passed')} | {row.get('tool_steps', '')} | {row.get('first_action_step', '')} | {row.get('redundant_exploration_steps', '')} | {row.get('exploration_warning_count', '')} | {row.get('input_tokens', '')} | {row.get('failure_category', '') or ''} |"
            )
        lines += [
            "",
            "## Key Findings",
            "",
            "Findings are populated only from completed real-model runs; dry-run and failed configuration rows are not capability evidence.",
            "",
            "## Resume-ready Metrics",
            "",
            f"- Real Task Pass Rate: {summary.get('pass_rate')}",
            f"- Mean Tool Steps: {summary.get('mean_tool_steps')}",
            f"- Mean Input Tokens: {summary.get('mean_input_tokens')}",
        ]
        (self.run_root / "report.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
