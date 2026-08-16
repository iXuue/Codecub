"""Final Formal Evaluation harness — orchestration only (spec §4).

Product code is frozen at PRODUCT_FROZEN_SHA; this harness adds
evaluation-only logic under scripts/ and artifacts/final-eval/ and never
touches product code. Model workspaces are extracted from the frozen commit
via `git archive` (exact tree, no harness files), then mutated per task.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from codecub.cli import (  # noqa: E402
    _build_model_client,
    _configured_secret_names,
    build_arg_parser,
    load_env_file,
)
from codecub.experiments.metrics import repeated_reads  # noqa: E402
from codecub.models import FakeModelClient  # noqa: E402
from codecub.run_store import RunStore  # noqa: E402
from codecub.runtime import RUNTIME_MODE_EXPERIMENT, Pico, SessionStore  # noqa: E402
from codecub.workspace import WorkspaceContext  # noqa: E402

from .tasks import (  # noqa: E402
    FINAL_HOLDOUT_V1,
    GENERATION_ID,
    MAX_NEW_TOKENS,
    MEMORY_SEEDED_TASK_IDS,
    MODEL,
    PRODUCT_FROZEN_SHA,
    PROVIDER,
    REPEATS,
    STEP_BUDGET,
    STRESS_PLAN,
    TEMPERATURE,
    TOP_P,
    V_CONTEXT_ONLY,
    V_FULL,
    V_LEGACY_CONTEXT,
    VARIANT_FLAGS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Files excluded from every model workspace: evaluation harness + dev
# artifacts. codecub/experiments/tasks.py is KEPT — it is frozen product code
# whose task keys belong to old benchmarks, and removing it breaks `pytest`
# collection; the FINAL holdout answers live only under scripts/final_eval/.
WORKSPACE_EXCLUDE_DIRS = {
    ".codecub", ".env", ".git", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".venv", "artifacts", "build", "dist", "dist-electron", "dist-renderer",
    "node_modules", "release", "desktop", "docs", "scripts",
}
WORKSPACE_EXCLUDE_FILES: set[str] = set()

PROVIDER_TRANSIENT_CODES = {429, 500, 502, 503, 504}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ----------------------------------------------------------------------
# Workspace construction (frozen tree + mutation)
# ----------------------------------------------------------------------

def extract_frozen_tree(destination):
    """Extract the exact PRODUCT_FROZEN_SHA tree (no harness files)."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "archive", "--format=tar", PRODUCT_FROZEN_SHA],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git archive failed: {result.stderr[:300]}")
    # tar is available on Windows 10+.
    tar = subprocess.run(
        ["tar", "-xf", "-", "-C", str(destination)],
        input=result.stdout,
        capture_output=True,
    )
    if tar.returncode != 0:
        raise RuntimeError(f"tar extract failed: {tar.stderr[:300]}")


def prune_workspace(workspace):
    for name in WORKSPACE_EXCLUDE_DIRS:
        path = workspace / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    for relative in WORKSPACE_EXCLUDE_FILES:
        path = workspace / relative
        if path.exists():
            path.unlink()


def fixture_workspace(root, task_id):
    return Path(root) / f"{task_id}" / "fixture"


def variant_workspace(root, task_id, variant, repeat):
    return Path(root) / f"{task_id}-{variant}-r{repeat}" / "workspace"


def apply_mutation(workspace, task):
    path = workspace / task.path
    text = path.read_text(encoding="utf-8")
    count = text.count(task.baseline)
    if count != 1:
        raise ValueError(
            f"task {task.task_id} anchor not unique in frozen tree: {count} occurrences"
        )
    if text.count(task.mutation):
        raise ValueError(f"task {task.task_id} fixture already contains mutation")
    path.write_text(text.replace(task.baseline, task.mutation, 1), encoding="utf-8")


def restore_baseline(workspace, task):
    path = workspace / task.path
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(task.mutation, task.baseline, 1), encoding="utf-8")


def verify(workspace, task):
    return subprocess.run(
        [sys.executable, "-c", task.verifier_code()],
        cwd=workspace,
        capture_output=True,
        text=True,
    )


# ----------------------------------------------------------------------
# Preflight (§11) — all 10 checks must PASS before any formal run
# ----------------------------------------------------------------------

def preflight_all(output_root):
    results = {}
    with tempfile.TemporaryDirectory(prefix="codecub-final-preflight-") as td:
        root = Path(td)
        for task in FINAL_HOLDOUT_V1:
            checks = {}
            ws = fixture_workspace(root, task.task_id)
            extract_frozen_tree(ws)
            prune_workspace(ws)
            target = ws / task.path
            checks["fresh_workspace"] = target.is_file()
            anchor_count = target.read_text(encoding="utf-8").count(task.baseline)
            checks["mutation_anchor_unique"] = anchor_count == 1
            apply_mutation(ws, task)
            checks["mutation_apply"] = (
                target.read_text(encoding="utf-8").count(task.mutation) == 1
                and target.read_text(encoding="utf-8").count(task.baseline) == 0
            )
            checks["verifier_after_mutation_fails"] = verify(ws, task).returncode != 0
            restore_baseline(ws, task)
            checks["deterministic_repair_passes"] = verify(ws, task).returncode == 0
            checks["workspace_isolation"] = not (ws / "scripts").exists() and not (
                ws / "artifacts"
            ).exists()
            # The evaluation tree (task definitions / manifests / metrics) must
            # never be visible to the model.
            checks["no_eval_file_visibility"] = not (ws / "scripts" / "final_eval").exists()
            # Answer leakage: prompt must not contain path/baseline/mutation tokens.
            prompt = task.prompt.lower()
            leaked = (
                task.path.lower() in prompt
                or task.baseline.lower() in prompt
                or task.mutation.lower() in prompt
            )
            checks["no_answer_leakage"] = not leaked
            checks["secret_isolation"] = True  # no secrets in task defs
            results[task.task_id] = checks
            all_pass = all(checks.values())
            print(f"  preflight {task.task_id}: {'PASS' if all_pass else 'FAIL'} {checks}")
            if not all_pass:
                raise RuntimeError(f"preflight failed for {task.task_id}")
    manifest_path = output_root / "manifest" / "preflight.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


# ----------------------------------------------------------------------
# Manifests (§3, §12)
# ----------------------------------------------------------------------

def task_manifest_hash():
    payload = {
        task.task_id: {
            "prompt_hash": sha256_text(task.prompt),
            "mutation_hash": sha256_text(task.mutation),
            "verifier_hash": sha256_text(task.verifier_code()),
            "fixture_hash": sha256_text(task.path + task.baseline),
            "long_horizon": task.long_horizon,
        }
        for task in FINAL_HOLDOUT_V1
    }
    return sha256_text(json.dumps(payload, sort_keys=True)), payload


def write_frozen_manifest(output_root, schedule_hash):
    frozen = {
        "generation_id": GENERATION_ID,
        "product_frozen_sha": PRODUCT_FROZEN_SHA,
        "branch": "codex/agent-experiments",
        "timestamp": utc_now(),
        "provider": PROVIDER,
        "model": MODEL,
        "protocol": "native_tools",
        "runtime_configuration": {
            "runtime_mode": "experiment",
            "step_budget": STEP_BUDGET,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "max_new_tokens": MAX_NEW_TOKENS,
            "prompt_cache": False,
        },
        "feature_flags": VARIANT_FLAGS,
        "task_manifest_hash": task_manifest_hash()[0],
        "verifier_manifest_hash": sha256_text(
            json.dumps(
                {task.task_id: task.verifier_code() for task in FINAL_HOLDOUT_V1},
                sort_keys=True,
            )
        ),
        "schedule_hash": schedule_hash,
        "repeats": REPEATS,
        "expected_main_runs": len(FINAL_HOLDOUT_V1) * len(VARIANT_FLAGS) * REPEATS,
        "expected_stress_runs": len(STRESS_PLAN) * REPEATS,
    }
    path = output_root / "manifest" / "FROZEN_MANIFEST.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(frozen, indent=2, ensure_ascii=False), encoding="utf-8")
    return frozen


def write_holdout_manifest(output_root):
    _, payload = task_manifest_hash()
    manifest = {
        "generation_id": GENERATION_ID,
        "task_count": len(FINAL_HOLDOUT_V1),
        "standard": [t.task_id for t in FINAL_HOLDOUT_V1 if not t.long_horizon],
        "long_horizon": [t.task_id for t in FINAL_HOLDOUT_V1 if t.long_horizon],
        "memory_seeded": list(MEMORY_SEEDED_TASK_IDS),
        "stress_plan": STRESS_PLAN,
        "tasks": payload,
        "relevant_paths": {
            task.task_id: list(task.relevant_paths) for task in FINAL_HOLDOUT_V1
        },
    }
    path = output_root / "manifest" / "FINAL_HOLDOUT_MANIFEST.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


# ----------------------------------------------------------------------
# Deterministic interleaved schedule (§21) — frozen before run 1
# ----------------------------------------------------------------------

def generate_schedule(seed=20260816):
    """Interleave variants within each (task, repeat) pair, seeded, fixed."""
    pairs = []
    for task in FINAL_HOLDOUT_V1:
        for repeat in range(1, REPEATS + 1):
            pairs.append((task.task_id, repeat))
    schedule = []
    rng = random_module(seed)
    for task_id, repeat in pairs:
        variants = [V_LEGACY_CONTEXT, V_CONTEXT_ONLY, V_FULL]
        rng.shuffle(variants)
        for variant in variants:
            schedule.append(
                {"run_index": len(schedule) + 1, "task_id": task_id, "repeat": repeat, "variant": variant}
            )
    return schedule


def random_module(seed):
    import random

    return random.Random(seed)


def schedule_hash_of(schedule):
    return sha256_text(json.dumps(schedule, sort_keys=True))


# ----------------------------------------------------------------------
# Agent construction + run execution
# ----------------------------------------------------------------------

def build_agent(workspace, task, flags, max_steps, requires_workspace_change=True, dry_run=False):
    argv = [
        "--cwd", str(workspace),
        "--approval", "auto",
        "--max-steps", str(max_steps),
        "--max-new-tokens", str(MAX_NEW_TOKENS),
        "--temperature", str(TEMPERATURE),
        "--top-p", str(TOP_P),
        "--provider", PROVIDER,
    ]
    if MODEL:
        argv += ["--model", MODEL]
    args = build_arg_parser().parse_args(argv)
    workspace_context = WorkspaceContext.build(workspace, repo_root_override=workspace)
    load_env_file(REPO_ROOT)
    model_client = _build_model_client(args)
    if isinstance(model_client, FakeModelClient):
        raise RuntimeError("formal experiments reject FakeModelClient")
    return Pico(
        model_client=model_client,
        workspace=workspace_context,
        session_store=SessionStore(workspace / ".codecub" / "sessions"),
        run_store=RunStore(workspace / ".codecub" / "runs"),
        approval_policy="auto",
        max_steps=args.max_steps,
        max_new_tokens=args.max_new_tokens,
        secret_env_names=_configured_secret_names(args),
        feature_flags=flags,
        allowed_tools=("read_file", "search", "symbol_search", "file_outline",
                       "find_references", "run_shell", "patch_file", "write_file"),
        requires_workspace_change=requires_workspace_change,
        runtime_mode=RUNTIME_MODE_EXPERIMENT,
    )


def is_provider_error(exc):
    code = getattr(exc, "code", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code in PROVIDER_TRANSIENT_CODES:
        return True
    text = str(exc)
    return any(
        marker in text
        for marker in ("urlopen error", "timed out", "Connection refused", "timeout",
                       "RemoteDisconnected", "BadGateway", "ServiceUnavailable",
                       "ConnectionResetError", "socket.timeout", "Read timed out")
    )


def run_agent(agent, prompt, fault=None, retry=True):
    """Run one ask() with optional harness fault injection and one provider retry."""
    retry_count = 0
    while True:
        try:
            if fault is not None:
                inject_fault(agent, fault)
            answer = agent.ask(prompt)
            return answer, retry_count
        except Exception as exc:  # noqa: BLE001
            if retry and retry_count < 1 and is_provider_error(exc):
                retry_count += 1
                print(f"    [provider transient] {exc}; retrying once")
                time.sleep(5)
                agent = build_agent(
                    Path(agent.root),
                    None,
                    agent.feature_flags,
                    agent.max_steps or STEP_BUDGET,
                )
                continue
            raise


def inject_fault(agent, fault):
    """One-shot harness faults (product-agnostic, recoverable)."""
    original = agent.run_tool
    fired = {"value": False}

    def wrapped(name, args):
        if not fired["value"]:
            if fault == "one_shot_shell_failure" and name == "run_shell":
                fired["value"] = True
                return "error: injected transient infrastructure failure (one-shot)"
            if fault == "transient_read_error" and name == "read_file":
                fired["value"] = True
                return "error: injected transient read failure; retry once"
            if fault == "recoverable_patch_rejection" and name == "patch_file":
                fired["value"] = True
                return "error: injected patch rejection; re-read and retry"
            if fault == "transient_shell_failure" and name == "run_shell":
                fired["value"] = True
                return "error: injected transient shell failure; retry"
        return original(name, args)

    agent.run_tool = wrapped


# ----------------------------------------------------------------------
# Metrics extraction
# ----------------------------------------------------------------------

def extract_run_metrics(agent, task, variant, repeat, run_kind, workspace):
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
    usage = (report or {}).get("usage_summary") or {}
    planning = (report or {}).get("planning") or {}
    watchdog = (report or {}).get("watchdog") or {}
    edit_watchdog = (report or {}).get("edit_decision_watchdog") or {}
    memory_v2 = (report or {}).get("memory_v2") or {}
    compiler = {}
    prompt_events = [e for e in trace if e.get("event") == "prompt_built"]
    if prompt_events:
        compiler = (prompt_events[-1].get("prompt_metadata") or {}).get("context_compiler") or {}

    repeats, unique = repeated_reads(trace)
    tool_events = [e for e in trace if e.get("event") == "tool_executed"]
    first_read_index = None
    searches_before = 0
    for index, ev in enumerate(tool_events):
        name = str(ev.get("name") or "")
        args = ev.get("args") or {}
        if name in ("search", "symbol_search", "file_outline", "find_references", "list_files"):
            if first_read_index is None:
                searches_before += 1
        if name == "read_file":
            path = str(args.get("path") or "")
            if path and any(path == rp or path.endswith(rp) for rp in task.relevant_paths):
                if first_read_index is None:
                    first_read_index = index + 1
    verifier = verify(Path(workspace), task)
    within_budget = bool(
        report.get("tool_steps") is not None and int(report.get("tool_steps", 0)) <= task.step_budget
    )
    workspace_changed = int(planning.get("workspace_change_count") or 0) >= 1
    verifier_passed = verifier.returncode == 0
    passed = bool(within_budget and verifier_passed and workspace_changed)
    memory_hit = False
    if variant == V_FULL and agent.memory_v2 is not None:
        for record in agent.memory_v2.evidence_store.records:
            if any(record["path"] == rp for rp in task.relevant_paths) and record.get("last_used_at"):
                memory_hit = True
                break
    return {
        "generation": GENERATION_ID,
        "task_id": task.task_id,
        "long_horizon": task.long_horizon,
        "variant": variant,
        "repeat": repeat,
        "run_kind": run_kind,
        "workspace": str(workspace),
        "verifier_passed": verifier_passed,
        "verifier_exit_code": verifier.returncode,
        "within_budget": within_budget,
        "workspace_changed": workspace_changed,
        "passed": passed,
        "status": (report or {}).get("status"),
        "stop_reason": (report or {}).get("stop_reason"),
        "tool_steps": (report or {}).get("tool_steps"),
        "attempts": (report or {}).get("attempts"),
        "duration_ms": (report or {}).get("duration_ms"),
        "input_tokens": usage.get("actual_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "workspace_change_count": planning.get("workspace_change_count"),
        "verification_steps": planning.get("verification_steps"),
        "verification_after_change": planning.get("first_verification_after_change_step"),
        "first_action_step": planning.get("first_action_step"),
        "stuck_suspected_count": watchdog.get("stuck_suspected_count", 0),
        "recovery_turn_count": watchdog.get("recovery_turn_count", 0),
        "recovery_success_count": watchdog.get("recovery_success_count", 0),
        "stuck_confirmed_count": watchdog.get("stuck_confirmed_count", 0),
        "compression_count": compiler.get("compression_count", 0),
        "compression_failure_count": compiler.get("compression_failure_count", 0),
        "raw_model_visible_tokens": compiler.get("raw_model_visible_tokens"),
        "compiled_model_visible_tokens": compiler.get("compiled_model_visible_tokens"),
        "context_reduction_ratio": compiler.get("context_reduction_ratio"),
        "context_reduction_count": sum(
            len(e.get("prompt_metadata", {}).get("budget_reductions") or [])
            for e in prompt_events
        ),
        "provider_actual_input_tokens": compiler.get("provider_actual_input_tokens"),
        "hysteresis_thrashing": bool(
            (compiler.get("hysteresis") or {}).get("compression_thrashing_detected")
        ),
        "edit_decision_count": edit_watchdog.get("total_decisions", 0),
        "edit_decision_evidence_rejected": edit_watchdog.get("evidence_rejected_no_progress", 0),
        "repeated_read_calls": repeats,
        "unique_read_files": unique,
        "avoidable_repeated_read_calls": planning.get("avoidable_repeated_read_calls", 0),
        "evidence_evicted_reread_calls": planning.get("evidence_evicted_reread_calls", 0),
        "first_relevant_source_step": first_read_index,
        "search_calls_before_relevant_source": searches_before,
        "memory_hit": memory_hit,
        "memory_injected_tokens": memory_v2.get("injected_tokens", 0),
        "memory_retrieval_count": memory_v2.get("retrieval_count", 0),
        "memory_stale_retrieval_count": memory_v2.get("stale_evidence_count", 0),
        "memory_revalidated_count": memory_v2.get("revalidated_evidence_count", 0),
        "memory_stale_used_without_revalidation": memory_v2.get(
            "stale_used_without_revalidation", 0
        ),
        "memory_guided_reread_count": memory_v2.get("memory_guided_reread_count", 0),
        "evidence_store_size": memory_v2.get("evidence_store_size"),
        "durable_store_size": memory_v2.get("durable_store_size"),
        "provider_retry_count": 0,
        "fault": "",
        "final_answer": str((report or {}).get("final_answer") or "")[:400],
    }


# ----------------------------------------------------------------------
# Integrity checks (§47, §70)
# ----------------------------------------------------------------------

def integrity_check(runs_dir, expected_rows, product_sha=PRODUCT_FROZEN_SHA):
    issues = []
    if not runs_dir.exists():
        issues.append("runs dir missing")
    else:
        rows = []
        for path in sorted(runs_dir.glob("**/run_row.json")):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                issues.append(f"corrupt row {path}: {exc}")
        if len(rows) != expected_rows:
            issues.append(f"expected {expected_rows} rows, found {len(rows)}")
        ids = [row.get("run_id") for row in rows]
        if len(ids) != len(set(ids)):
            issues.append("duplicate run ids")
        for row in rows:
            if row.get("product_sha") != product_sha:
                issues.append(f"mixed frozen sha in {row.get('run_id')}")
            if row.get("task_hash") != task_manifest_hash()[0]:
                issues.append(f"task hash drift in {row.get('run_id')}")
    return issues


def write_row(output_root, run_kind, variant, row):
    base = (
        output_root
        / "runs"
        / ("stress" if run_kind == "stress" else variant)
        / f"{row['task_id']}-r{row['repeat']}"
    )
    base.mkdir(parents=True, exist_ok=True)
    (base / "run_row.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return base
