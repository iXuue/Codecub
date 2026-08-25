"""Phase 3 Fast Validation — 3 development tasks × Memory OFF / Memory 2.0 ON.

Development validation only — never a Formal Memory Ablation (spec §83).

Design (spec §77-§89):
- Task A  cross-session location reuse: Session A discovers the runtime loop
          file (evidence); Session B must locate and fix it.
- Task B  durable workflow recall: Session A verifies the test command
          (durable build-and-test); Session B must fix + verify.
- Task C  stale evidence safety: Session A records evidence on baseline;
          the file is then mutated out-of-band; Session B must re-read
          instead of trusting the stale hint.

Per task: ONE real Session A seed run produces the memory, then the seeded
workspace is copied for the OFF and ON variants. Session B is always a
FRESH session; the only cross-session information is the on-disk Evidence /
Durable stores (spec §66 — no session history is copied).

Fairness (spec §81): same provider / model / task / fixture / runtime /
context / budget / verifier; the only variable is Memory 2.0.
prompt_cache is disabled (spec §82).

Total API: 3 seeds + 6 measured runs; measured runs may retry at most twice
on provider failure (spec §77).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codecub.cli import (  # noqa: E402
    _build_model_client,
    _configured_secret_names,
    build_arg_parser,
    load_env_file,
)
from codecub.experiments.metrics import extract_metrics, repeated_reads  # noqa: E402
from codecub.experiments.tasks import ExperimentTask  # noqa: E402
from codecub.models import FakeModelClient  # noqa: E402
from codecub.run_store import RunStore  # noqa: E402
from codecub.runtime import RUNTIME_MODE_EXPERIMENT, Pico, SessionStore  # noqa: E402
from codecub.workspace import WorkspaceContext  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

ON_FLAGS = {
    "memory": True,
    "relevant_memory": True,
    "memory_v2": True,
    "evidence_memory": True,
    "durable_memory": True,
    "context_reduction": True,
    "context_compiler": True,
    "prompt_cache": False,
}
OFF_FLAGS = {
    "memory": False,
    "relevant_memory": False,
    "memory_v2": False,
    "evidence_memory": False,
    "durable_memory": False,
    "context_reduction": True,
    "context_compiler": True,
    "prompt_cache": False,
}

TASKS = (
    ExperimentTask(
        id="memory2_a_location_reuse",
        category="cross-session-location",
        prompt="Investigate why normal sessions no longer retain working memory and restore the intended default without changing the public API.",
        path="codecub/runtime.py",
        baseline='    "memory": True,',
        mutation='    "memory": False,',
        step_budget=24,
        requires_workspace_change=True,
        metadata={
            "evaluation_role": "development",
            "memory2_task": "A",
            "seed_mutation_order": "before",
            "seed_prompt": (
                "Inspect this repository and report: (1) which file implements the runtime "
                "loop that answers a user question, and (2) the default value of the memory "
                "feature flag. Do not modify any files."
            ),
        },
    ),
    ExperimentTask(
        id="memory2_b_workflow_recall",
        category="durable-workflow-recall",
        prompt="Run records are missing model-attempt accounting after a task state regression. Restore the correct state update and make sure reports remain consistent.",
        path="codecub/task_state.py",
        baseline="        self.attempts += 1",
        mutation="        self.attempts += 0",
        step_budget=24,
        requires_workspace_change=True,
        metadata={
            "evaluation_role": "development",
            "memory2_task": "B",
            "seed_mutation_order": "after",
            "seed_prompt": (
                "Run the project's test suite once using the standard test command and report "
                "the exact command and its outcome. Do not modify any files."
            ),
        },
    ),
    ExperimentTask(
        id="memory2_c_stale_safety",
        category="stale-evidence-safety",
        prompt="Investigate why normal sessions no longer retain working memory and restore the intended default without changing the public API.",
        path="codecub/runtime.py",
        baseline='    "memory": True,',
        mutation='    "memory": False,',
        step_budget=24,
        requires_workspace_change=True,
        metadata={
            "evaluation_role": "development",
            "memory2_task": "C",
            "seed_mutation_order": "after",
            "seed_prompt": (
                "Locate where the runtime feature flags are defined and report the file path "
                "and the default value of the memory flag. Do not modify any files."
            ),
        },
    ),
)

IGNORED_NAMES = {
    ".codecub", ".env", ".env.local", ".env.development", ".env.production",
    ".electron-cache", ".git", ".next", ".pytest_cache", ".ruff_cache", ".tmp",
    ".uv-cache", ".venv", "__pycache__", "artifacts", "build", "dist",
    "dist-electron", "dist-renderer", "node_modules", "release",
    # Development artifacts of CodeCub itself, not the project under test:
    # docs/ contains the Phase 3 design doc that names the mutation location;
    # scripts/ contains this runner and the development probes.
    "docs", "scripts", "desktop",
}

# Benchmark answer keys: the experiment task definitions contain the exact
# baseline/mutation fragments for every task. A faithful fixture must not
# include them, or the model can "solve" the task by reading the harness.
EXTRA_EXCLUDE_PATHS = {
    "codecub/experiments/tasks.py",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def copy_workspace(src, dst, keep_codecub=False):
    def ignore(_directory, names):
        ignored = {
            name
            for name in names
            if (name in IGNORED_NAMES and not (keep_codecub and name == ".codecub"))
            or name.endswith(".pyc")
        }
        return ignored

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=ignore)
    for relative in EXTRA_EXCLUDE_PATHS:
        target = dst / relative
        if target.exists():
            target.unlink()


def copy_seed_memory(src_fixture, dst_fixture):
    """Carry over a previously produced seed memory into a clean fixture.

    Only the Memory 2.0 stores (.codecub/memory/v2) are copied — NEVER the
    seed's session history / runs / usage (spec §66: Session B must not
    receive Session A's history).
    """
    src_v2 = Path(src_fixture) / ".codecub" / "memory" / "v2"
    if not src_v2.exists():
        raise ValueError(f"seed workspace has no v2 memory store: {src_v2}")
    dst_v2 = Path(dst_fixture) / ".codecub" / "memory" / "v2"
    dst_v2.mkdir(parents=True, exist_ok=True)
    for path in src_v2.iterdir():
        if path.is_file():
            shutil.copy2(path, dst_v2 / path.name)


def make_isolated_workspaces(root, task_id):
    """Return (fixture, off_ws, on_ws) with no sibling relationships:
    each workspace lives in its own parent directory, so `dir ..` from a
    running agent can never reach the fixture or the other variant, and the
    workspace root is outside the source repository (no git discovery).
    """
    fixture = root / f"{task_id}" / "fixture"
    off_ws = root / f"{task_id}-off" / "workspace"
    on_ws = root / f"{task_id}-on" / "workspace"
    return fixture, off_ws, on_ws


class FastValidationRunner:
    def __init__(self, args):
        self.args = args
        self.provider = args.provider or "deepseek"
        self.model = args.model
        self.output_root = Path(args.output_dir).resolve() / (
            f"phase3-fast-validation-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        )
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.rows = []
        self.seed_rows = []
        self.seed_workspace_root = (
            Path(args.seed_workspace_dir).resolve() if args.seed_workspace_dir else None
        )
        wanted = set(args.tasks or [])
        self.tasks = [
            task for task in TASKS if not wanted or task.metadata.get("memory2_task") in wanted
        ]

    # ------------------------------------------------------------------
    # Fixture helpers (mirror ExperimentRunner; no side-effect dirs)
    # ------------------------------------------------------------------

    def preflight_task(self, task, workspace):
        path = workspace / task.path
        text = path.read_text(encoding="utf-8")
        count = text.count(task.baseline)
        if count != 1:
            raise ValueError(
                f"task {task.id} is stale: expected 1 baseline occurrence in {task.path}, found {count}"
            )
        if text.count(task.mutation):
            raise ValueError(f"task {task.id} is stale: fixture already contains mutation")
        return {"path": path, "text": text}

    def apply_mutation(self, task, workspace):
        preflight = self.preflight_task(task, workspace)
        preflight["path"].write_text(
            preflight["text"].replace(task.baseline, task.mutation, 1), encoding="utf-8"
        )

    def verify(self, task, workspace):
        code = (
            f"from pathlib import Path; text=Path({task.path!r}).read_text(encoding='utf-8'); "
            f"assert text.count({task.baseline!r}) == 1; assert text.count({task.mutation!r}) == 0"
        )
        return subprocess.run(
            [sys.executable, "-c", code], cwd=workspace, capture_output=True, text=True
        )

    # ------------------------------------------------------------------
    # Agent construction (reuses CLI provider assembly, never FakeModelClient)
    # ------------------------------------------------------------------

    def _build_agent(self, workspace, task, flags, max_steps, requires_workspace_change):
        argv = [
            "--cwd", str(workspace),
            "--approval", self.args.approval,
            "--max-steps", str(max_steps),
            "--max-new-tokens", str(self.args.max_new_tokens),
            "--temperature", str(self.args.temperature),
            "--top-p", str(self.args.top_p),
        ]
        if self.provider:
            argv += ["--provider", self.provider]
        if self.model:
            argv += ["--model", self.model]
        if self.args.dry_run:
            return self._dry_run_agent(workspace, task, flags, max_steps, requires_workspace_change)
        args = build_arg_parser().parse_args(argv)
        workspace_context = WorkspaceContext.build(workspace, repo_root_override=workspace)
        load_env_file(REPO_ROOT)
        model_client = _build_model_client(args)
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
            feature_flags=flags,
            context_window=self.args.context_window,
            allowed_tools=("read_file", "search", "symbol_search", "file_outline",
                           "find_references", "run_shell", "patch_file", "write_file"),
            requires_workspace_change=requires_workspace_change,
            runtime_mode=RUNTIME_MODE_EXPERIMENT,
        )
        return agent

    def _dry_run_agent(self, workspace, task, flags, max_steps, requires_workspace_change):
        """Scripted FakeModelClient to validate plumbing end-to-end (no API cost)."""
        outputs = [
            f'<tool>{{"name":"read_file","args":{{"path":"{task.path}","start":1,"end":80}}}}</tool>',
            f'<tool>{{"name":"patch_file","args":{{"path":"{task.path}","old_text":"{task.mutation}","new_text":"{task.baseline}"}}}}</tool>',
            '<tool>{"name":"run_shell","args":{"command":"python -m pytest --version","timeout":20}}</tool>',
            "<final>Dry-run fix applied.</final>",
        ]
        workspace_context = WorkspaceContext.build(workspace, repo_root_override=workspace)
        agent = Pico(
            model_client=FakeModelClient(outputs),
            workspace=workspace_context,
            session_store=SessionStore(workspace / ".codecub" / "sessions"),
            run_store=RunStore(workspace / ".codecub" / "runs"),
            approval_policy="auto",
            max_steps=max_steps,
            max_new_tokens=self.args.max_new_tokens,
            feature_flags=flags,
            allowed_tools=("read_file", "search", "symbol_search", "file_outline",
                           "find_references", "run_shell", "patch_file", "write_file"),
            requires_workspace_change=requires_workspace_change,
            runtime_mode=RUNTIME_MODE_EXPERIMENT,
        )
        return agent

    def _run_agent(self, agent, prompt):
        started = time.monotonic()
        answer = agent.ask(prompt)
        duration_ms = int((time.monotonic() - started) * 1000)
        task_state = agent.current_task_state
        trace = []
        if task_state is not None:
            trace_path = agent.run_store.trace_path(task_state)
            if trace_path.exists():
                trace = [
                    json.loads(line)
                    for line in trace_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
        report = agent.build_report(task_state) if task_state is not None else {}
        return answer, report, trace, duration_ms

    # ------------------------------------------------------------------
    # Seed (Session A) — real run that produces the memory
    # ------------------------------------------------------------------

    def run_seed(self, task, workspace):
        seed_prompt = str(task.metadata.get("seed_prompt") or task.prompt)
        flags = dict(ON_FLAGS)
        if self.args.dry_run:
            # Scripted seed: read the target file so evidence is recorded.
            agent = self._dry_run_agent(
                workspace, task, flags, max_steps=12, requires_workspace_change=False
            )
            agent.model_client.outputs = [
                f'<tool>{{"name":"read_file","args":{{"path":"{task.path}","start":1,"end":40}}}}</tool>',
                "<final>Seed discovery done.</final>",
            ]
        else:
            agent = self._build_agent(
                workspace, task, flags, max_steps=16, requires_workspace_change=False
            )
        answer, report, trace, duration_ms = self._run_agent(agent, seed_prompt)
        v2 = agent.memory_v2
        row = {
            "task_id": task.id,
            "phase": "seed",
            "answer": answer[:500],
            "duration_ms": duration_ms,
            "evidence_count": v2.evidence_store.size(),
            "durable_count": v2.durable_store.size(),
            "migration": v2.last_migration.to_dict() if v2.last_migration else None,
            "evidence_paths": [
                r["path"] for r in v2.evidence_store.latest_records()
            ][:20],
            "durable_statements": [
                r["statement"] for r in v2.durable_store.active_records()
            ][:10],
        }
        self.seed_rows.append(row)
        return row

    # ------------------------------------------------------------------
    # Session B (measured) — fresh session, variant-controlled memory
    # ------------------------------------------------------------------

    def run_session_b(self, task, workspace, variant):
        flags = ON_FLAGS if variant == "on" else OFF_FLAGS
        agent = self._build_agent(
            workspace, task, flags, max_steps=task.step_budget,
            requires_workspace_change=True,
        )
        answer, report, trace, duration_ms = self._run_agent(agent, task.prompt)
        row = extract_metrics(report, trace)
        row.update(
            {
                "task_id": task.id,
                "variant": variant,
                "task_category": task.category,
                "answer": answer[:500],
                "duration_ms": duration_ms,
                "memory_v2_report": report.get("memory_v2") or {},
                "memory_v2_activity": report.get("memory_v2_activity") or {},
                "memory_migration": report.get("memory_migration"),
            }
        )
        # Location metrics (Task A / C): first read of the target path.
        first_read_index = None
        searches_before = 0
        tool_events = [ev for ev in trace if ev.get("event") == "tool_executed"]
        for index, ev in enumerate(tool_events):
            name = str(ev.get("name") or "")
            args = ev.get("args") or {}
            if name in ("search", "symbol_search", "file_outline", "find_references", "list_files"):
                if first_read_index is None:
                    searches_before += 1
            if name == "read_file":
                path = str(args.get("path") or "")
                if path and (path == task.path or path.endswith(task.path)):
                    if first_read_index is None:
                        first_read_index = index + 1
        row["first_relevant_read_step"] = first_read_index
        row["search_calls_before_relevant_read"] = searches_before
        repeats, unique = repeated_reads(trace)
        row["repeated_read_calls"] = repeats
        row["unique_read_files"] = unique
        # Memory hit: an evidence record for the target path was delivered (used).
        # Check ALL records (a delivered seed record may later be superseded by
        # the model's own re-read — that is correct stale-revalidation behavior).
        memory_hit = False
        if variant == "on":
            for record in agent.memory_v2.evidence_store.records:
                if record["path"] == task.path and record.get("last_used_at"):
                    memory_hit = True
                    break
        row["memory_hit"] = memory_hit
        # Task B: did ON use the remembered test command?
        row["used_remembered_test_command"] = False
        if variant == "on" and task.id == "memory2_b_workflow_recall":
            remembered = [
                r["statement"] for r in agent.memory_v2.durable_store.active_records()
                if r["topic"] == "build-and-test"
            ]
            for ev in tool_events:
                if ev.get("name") == "run_shell":
                    command = str((ev.get("args") or {}).get("command") or "")
                    if any(
                        "pytest" in command
                        and any(word in command for word in r.split() if word not in ("Test", "command", "is:"))
                        for r in remembered
                    ):
                        row["used_remembered_test_command"] = True
        # Verifier.
        verifier = self.verify(task, workspace)
        row["verifier_passed"] = verifier.returncode == 0
        row["verifier_exit_code"] = verifier.returncode
        row["within_budget"] = bool(
            report.get("tool_steps") is not None
            and int(report.get("tool_steps", 0)) <= task.step_budget
        )
        row["passed"] = bool(row["within_budget"] and row["verifier_passed"])
        return row

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self):
        # Workspaces live outside the source repository (system temp), so a
        # running agent cannot discover the parent repo's .git or reach the
        # fixture/other-variant via `..`.
        import tempfile

        workspace_root = Path(
            tempfile.mkdtemp(prefix="codecub-fv-")
        )
        for task in self.tasks:
            task_root = self.output_root / task.id
            task_root.mkdir(parents=True, exist_ok=True)
            # 1) Fresh fixture workspace (no docs/scripts/desktop — see
            #    IGNORED_NAMES; no task-definition answer keys).
            fixture, off_ws, on_ws = make_isolated_workspaces(workspace_root, task.id)
            copy_workspace(REPO_ROOT, fixture, keep_codecub=False)
            self.preflight_task(task, fixture)
            order = str(task.metadata.get("seed_mutation_order") or "before")
            if order == "before":
                self.apply_mutation(task, fixture)
            # 2) Session A seed: run fresh, or carry over an existing seed memory.
            if self.seed_workspace_root is not None:
                seed_source = self.seed_workspace_root / task.id / "fixture"
                copy_seed_memory(seed_source, fixture)
                seed_row = {
                    "task_id": task.id,
                    "phase": "seed",
                    "carried_over": True,
                    "source": str(seed_source),
                }
                self.seed_rows.append(seed_row)
            else:
                seed_row = self.run_seed(task, fixture)
            if order == "after":
                self.apply_mutation(task, fixture)
            self._write_json(task_root / "seed.json", seed_row)
            # 3) Variant workspaces: identical seeded memory, fresh Session B.
            #    Each workspace is isolated (own parent dir); the fixture is
            #    deleted afterwards so no diff source exists.
            for variant, ws in (("off", off_ws), ("on", on_ws)):
                copy_workspace(fixture, ws, keep_codecub=True)
            shutil.rmtree(fixture, ignore_errors=True)
            for variant, ws in (("off", off_ws), ("on", on_ws)):
                row = self.run_session_b(task, ws, variant)
                row["run_id"] = f"{task.id}-{variant}-{uuid.uuid4().hex[:6]}"
                row["workspace"] = str(ws)
                self.rows.append(row)
                self._write_json(task_root / f"session-b-{variant}.json", row)
                print(
                    f"[phase3-fast] {task.id} {variant}: "
                    f"verifier={row.get('verifier_passed')} steps={row.get('tool_steps')} "
                    f"first_read={row.get('first_relevant_read_step')} "
                    f"search_before={row.get('search_calls_before_relevant_read')} "
                    f"repeats={row.get('repeated_read_calls')} "
                    f"mem_hit={row.get('memory_hit')} "
                    f"injected_tokens={row.get('memory_injected_tokens')}"
                )
        self._write_json(self.output_root / "runs.json", {"seeds": self.seed_rows, "runs": self.rows})
        summary = self.summarize()
        self._write_json(self.output_root / "summary.json", summary)
        self._write_markdown(summary)
        return summary

    def summarize(self):
        def mean(key, rows=None):
            values = [
                row[key] for row in (rows or self.rows)
                if isinstance(row.get(key), (int, float))
            ]
            return round(sum(values) / len(values), 2) if values else None

        off = [row for row in self.rows if row.get("variant") == "off"]
        on = [row for row in self.rows if row.get("variant") == "on"]
        return {
            "runs_total": len(self.rows),
            "runs_off": len(off),
            "runs_on": len(on),
            "verifier_passes_off": sum(bool(row.get("verifier_passed")) for row in off),
            "verifier_passes_on": sum(bool(row.get("verifier_passed")) for row in on),
            "mean_tool_steps_off": mean("tool_steps", off),
            "mean_tool_steps_on": mean("tool_steps", on),
            "mean_first_relevant_read_off": mean("first_relevant_read_step", off),
            "mean_first_relevant_read_on": mean("first_relevant_read_step", on),
            "mean_search_before_off": mean("search_calls_before_relevant_read", off),
            "mean_search_before_on": mean("search_calls_before_relevant_read", on),
            "repeated_reads_off": mean("repeated_read_calls", off),
            "repeated_reads_on": mean("repeated_read_calls", on),
            "stale_used_without_revalidation": mean("memory_stale_used_without_revalidation", on),
            "memory_guided_reread_count": mean("memory_guided_reread_count", on),
            "memory_injected_tokens_on": mean("memory_injected_tokens", on),
            "memory_retrieval_count_on": mean("memory_retrieval_count", on),
            "memory_hit_on": sum(bool(row.get("memory_hit")) for row in on),
            "seeds": [
                {
                    "task_id": row.get("task_id"),
                    "evidence_count": row.get("evidence_count"),
                    "durable_count": row.get("durable_count"),
                }
                for row in self.seed_rows
            ],
            "rows": self.rows,
        }

    def _write_markdown(self, summary):
        lines = [
            "# Phase 3 Fast Validation (development)",
            "",
            f"- Runs: {summary['runs_total']} measured (OFF {summary['runs_off']} / ON {summary['runs_on']}) + "
            f"{len(summary['seeds'])} Session A seeds",
            f"- Verifier passes — OFF: {summary['verifier_passes_off']}/{summary['runs_off']}  "
            f"ON: {summary['verifier_passes_on']}/{summary['runs_on']}",
            f"- Mean tool steps — OFF: {summary['mean_tool_steps_off']}  ON: {summary['mean_tool_steps_on']}",
            f"- Mean first relevant read step — OFF: {summary['mean_first_relevant_read_off']}  "
            f"ON: {summary['mean_first_relevant_read_on']}",
            f"- Mean search calls before relevant read — OFF: {summary['mean_search_before_off']}  "
            f"ON: {summary['mean_search_before_on']}",
            f"- Repeated reads — OFF: {summary['repeated_reads_off']}  ON: {summary['repeated_reads_on']}",
            f"- Memory: injected tokens {summary['memory_injected_tokens_on']}, "
            f"retrieval count {summary['memory_retrieval_count_on']}, "
            f"memory hit {summary['memory_hit_on']}/{summary['runs_on']}, "
            f"stale-used-without-revalidation {summary['stale_used_without_revalidation']}",
            "",
            "Seeds:",
        ]
        for seed in summary["seeds"]:
            lines.append(
                f"- {seed['task_id']}: evidence={seed.get('evidence_count')} durable={seed.get('durable_count')}"
            )
        lines.append("")
        lines.append("Per-run detail:")
        for row in self.rows:
            lines.append(
                f"- {row.get('task_id')} {row.get('variant')}: verifier={row.get('verifier_passed')} "
                f"steps={row.get('tool_steps')} first_read={row.get('first_relevant_read_step')} "
                f"search_before={row.get('search_calls_before_relevant_read')} "
                f"repeats={row.get('repeated_read_calls')} mem_hit={row.get('memory_hit')} "
                f"mem_inj_tokens={row.get('memory_injected_tokens')} "
                f"stale_unrevalidated={row.get('memory_stale_used_without_revalidation')}"
            )
        (self.output_root / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _write_json(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 3 Fast Validation (development)")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default=None)
    parser.add_argument("--output-dir", default="artifacts/phase3-fast-validation")
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--approval", choices=("ask", "auto", "never"), default="auto")
    parser.add_argument("--context-window", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--tasks", action="append", default=[])
    parser.add_argument("--seed-workspace-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    runner = FastValidationRunner(args)
    summary = runner.run()
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
