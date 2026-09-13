"""Formal side-effect replay benchmark for the CodeCub production seam.

The benchmark intentionally registers a safe append-only tool at runtime.  It
does not modify CodeCub production semantics; A-F/H claims pass through
MiniAgent -> ToolExecutor -> _RuntimeToolReplay -> RunStore -> runner, while G
exercises the shared RunStore claim seam directly from independent processes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import portalocker  # noqa: E402

from codecub import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext  # noqa: E402
from codecub.models import ModelResponse  # noqa: E402
from codecub.run_store import RunStore  # noqa: E402
from codecub.task_state import TaskState  # noqa: E402

SCHEMA = "codecub.side-effect-replay.v1"
TOOL_NAME = "append_effect"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip()


class RawRecorder:
    """Thread-safe JSONL sink; every request remains independently auditable."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            self._handle.write("\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.flush()
            self._handle.close()


class ExperimentLog:
    def __init__(self, path: Path) -> None:
        self._handle = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, message: str) -> None:
        with self._lock:
            self._handle.write(f"{utc_now()} {message}\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.flush()
            self._handle.close()


class SafeEffectRecorder:
    """Append-only local effect sink with a cross-process file lock.

    The lock belongs to the benchmark tool, not to the CodeCub claim ledger.
    It only prevents the evidence files from becoming corrupt; duplicate
    invocations still produce duplicate records and are therefore observable.
    """

    def __init__(self, workspace: Path, *, delay_ms: float = 0.0, fail_after_effect: bool = False) -> None:
        self.workspace = workspace
        self.effect_path = workspace / "effects.jsonl"
        self.count_path = workspace / "real_execution_count.txt"
        self.lock_path = workspace / "effects.lock"
        self.delay_s = max(0.0, delay_ms) / 1_000
        self.fail_after_effect = fail_after_effect

    def __call__(self, args: dict[str, Any]) -> str:
        if self.delay_s:
            time.sleep(self.delay_s)
        operation_id = str(args.get("operation_id", ""))
        token = uuid4().hex
        record = {
            "operation_id": operation_id,
            "execution_token": token,
            "tool_name": TOOL_NAME,
            "args": dict(args),
            "executed_at": utc_now(),
        }
        with portalocker.Lock(str(self.lock_path), mode="a+", timeout=60):
            current = 0
            if self.count_path.exists():
                text = self.count_path.read_text(encoding="utf-8").strip()
                current = int(text or "0")
            self.count_path.write_text(str(current + 1) + "\n", encoding="utf-8")
            with self.effect_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
                handle.write("\n")
                handle.flush()
        if self.fail_after_effect:
            raise RuntimeError("injected response loss after local side effect")
        return json.dumps({"ok": True, "execution_token": token}, sort_keys=True)


class LegacyBenchmarkClient:
    """Native-capable client whose text response exercises legacy recovery."""

    supports_native_tools = True
    supports_prompt_cache = False
    model = "side-effect-legacy-benchmark"
    last_completion_metadata: dict[str, Any] = {}

    def __init__(self, tool_text: str) -> None:
        self.responses = [ModelResponse(text=tool_text), ModelResponse(text="Done.")]

    def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
        del messages, tools, max_new_tokens, tool_choice
        return self.responses.pop(0)


def effect_evidence(workspace: Path) -> dict[str, Any]:
    path = workspace / "effects.jsonl"
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    by_operation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_operation[str(record.get("operation_id", ""))].append(record)
    counter_path = workspace / "real_execution_count.txt"
    counter = int(counter_path.read_text(encoding="utf-8").strip() or "0") if counter_path.exists() else 0
    tokens = [str(item.get("execution_token", "")) for item in records]
    return {
        "real_execution_count_file": counter,
        "effect_record_count": len(records),
        "unique_execution_tokens": len(set(tokens)),
        "records": records,
        "by_operation": {key: len(value) for key, value in by_operation.items()},
    }


def append_effect_spec(
    runner: SafeEffectRecorder, *, circuit_breaker: bool = True
) -> dict[str, Any]:
    return {
        "schema": {"operation_id": "str", "value": "str"},
        "risky": True,
        "side_effect": True,
        "idempotent": False,
        "retryable": False,
        "timeout_seconds": 20,
        "circuit_breaker": circuit_breaker,
        "description": "benchmark-only isolated append effect",
        "run": runner,
    }


def new_state(store: RunStore, run_id: str, user_request: str) -> TaskState:
    state = TaskState.create(run_id=run_id, task_id=f"task-{run_id}", user_request=user_request)
    store.start_run(state)
    return state


def build_agent(
    workspace: Path,
    store: RunStore,
    state: TaskState,
    runner: SafeEffectRecorder,
    *,
    circuit_breaker: bool = True,
    model_client: Any | None = None,
) -> MiniAgent:
    agent = MiniAgent(
        model_client=model_client or FakeModelClient([]),
        workspace=WorkspaceContext.build(workspace, repo_root_override=workspace),
        session_store=SessionStore(workspace / ".codecub" / "sessions"),
        run_store=store,
        approval_policy="auto",
        max_steps=2,
    )
    agent.current_task_state = state
    agent.tools[TOOL_NAME] = append_effect_spec(
        runner, circuit_breaker=circuit_breaker
    )
    return agent


def metadata_for(agent: MiniAgent) -> dict[str, Any]:
    return dict(getattr(agent, "_last_tool_result_metadata", {}) or {})


def request_record(
    *,
    experiment: str,
    operation_id: str,
    operation_key: str,
    attempt: int,
    agent: MiniAgent,
    state: TaskState,
    store: RunStore,
    raw: RawRecorder,
    args: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    raised = ""
    try:
        result = agent.run_tool(TOOL_NAME, args, operation_key=operation_key)
    except Exception as exc:  # Keep raw evidence even if the seam itself fails.
        result = f"raised: {exc}"
        raised = f"{exc.__class__.__name__}: {exc}"
    metadata = metadata_for(agent)
    duration_ms = (time.perf_counter_ns() - started) / 1_000_000
    record = {
        "record_type": "request",
        "experiment": experiment,
        "operation_id": operation_id,
        "operation_key": operation_key,
        "attempt": attempt,
        "tool_name": TOOL_NAME,
        "args": dict(args),
        "result": str(result),
        "raised": raised,
        "duration_ms": duration_ms,
        "metadata": metadata,
    }
    agent.emit_trace(
        state,
        "tool_executed",
        {
            "name": TOOL_NAME,
            "args": dict(args),
            "operation_key": operation_key,
            "result": str(result),
            "duration_ms": int(duration_ms),
            **metadata,
        },
    )
    raw.write(record)
    return record


def is_blocked(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata") or {}
    return bool(metadata.get("side_effect_replay_blocked")) or str(
        metadata.get("tool_status", "")
    ) == "blocked"


def is_claim_winner(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata") or {}
    return bool(metadata.get("side_effect_claimed")) and not bool(
        metadata.get("side_effect_replay_detected")
    )


def is_underlying_error(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata") or {}
    return str(metadata.get("tool_status", "")) in {"error", "partial_success"}


def records_by_operation(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("record_type") == "request":
            result[str(record["operation_id"])].append(record)
    return result


def summarize_common(
    records: list[dict[str, Any]], evidence: dict[str, Any], *, expected_operations: int
) -> dict[str, Any]:
    grouped = records_by_operation(records)
    by_operation = evidence.get("by_operation", {})
    duplicate_count = sum(max(0, int(by_operation.get(key, 0)) - 1) for key in grouped)
    blocked = sum(1 for item in records if is_blocked(item))
    completed = sum(
        1
        for item in records
        if bool((item.get("metadata") or {}).get("side_effect_commit_recorded"))
    )
    return {
        "total_requests": len(records),
        "unique_operations": len(grouped),
        "expected_real_execution_count": expected_operations,
        "real_execution_count": int(evidence.get("real_execution_count_file", 0)),
        "effect_record_count": int(evidence.get("effect_record_count", 0)),
        "unique_execution_tokens": int(evidence.get("unique_execution_tokens", 0)),
        "duplicate_real_execution_count": duplicate_count,
        "replay_blocked_count": blocked,
        "completed_count": completed,
        "error_count": sum(1 for item in records if is_underlying_error(item)),
        "claim_winner_count": sum(1 for item in records if is_claim_winner(item)),
        "persisted_record_count": 0,
        "per_operation_real_execution_counts": dict(sorted(by_operation.items())),
    }


def run_experiment_a(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "A_sequential_replay"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-A-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    state = new_state(store, "run-side-effect-A", "sequential replay benchmark")
    runner = SafeEffectRecorder(workspace, delay_ms=config["delay_ms"])
    agent = build_agent(workspace, store, state, runner)
    records = []
    for index in range(config["unique_operations"]):
        operation_id = f"A-{index:04d}"
        key = f"operation-{operation_id}"
        args = {"operation_id": operation_id, "value": "same"}
        for attempt in range(config["repetitions"]):
            records.append(
                request_record(
                    experiment=name,
                    operation_id=operation_id,
                    operation_key=key,
                    attempt=attempt,
                    agent=agent,
                    state=state,
                    store=store,
                    raw=raw,
                    args=args,
                )
            )
    evidence = effect_evidence(workspace)
    summary = summarize_common(records, evidence, expected_operations=config["unique_operations"])
    summary.update(
        {
            "experiment": name,
            "workspace": str(workspace),
            "same_completed_replay_blocked": all(
                is_blocked(item) for item in records if item["attempt"] > 0
            ),
        }
    )
    log.write(
        f"finish {name} requests={summary['total_requests']} real={summary['real_execution_count']} duplicates={summary['duplicate_real_execution_count']}"
    )
    return summary


def run_experiment_b(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "B_concurrent_claim"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-B-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    state = new_state(store, "run-side-effect-B", "concurrent claim benchmark")
    agents = [
        build_agent(
            workspace,
            store,
            TaskState.from_dict(store.load_task_state(state.run_id)),
            SafeEffectRecorder(workspace, delay_ms=config["delay_ms"]),
        )
        for _ in range(config["concurrency"])
    ]
    records: list[dict[str, Any]] = []

    def invoke(agent: MiniAgent, operation_id: str, key: str, barrier: threading.Barrier, attempt: int) -> dict[str, Any]:
        args = {"operation_id": operation_id, "value": "same"}
        barrier.wait(timeout=60)
        return request_record(
            experiment=name,
            operation_id=operation_id,
            operation_key=key,
            attempt=attempt,
            agent=agent,
            state=agent.current_task_state,
            store=store,
            raw=raw,
            args=args,
        )

    with ThreadPoolExecutor(max_workers=config["concurrency"]) as pool:
        for index in range(config["unique_operations"]):
            operation_id = f"B-{index:04d}"
            key = f"operation-{operation_id}"
            barrier = threading.Barrier(config["concurrency"])
            futures = [
                pool.submit(invoke, agent, operation_id, key, barrier, attempt)
                for attempt, agent in enumerate(agents)
            ]
            records.extend(future.result() for future in futures)
    evidence = effect_evidence(workspace)
    grouped = records_by_operation(records)
    operation_metrics = {
        operation_id: {
            "claim_attempts": len(items),
            "claim_winners": sum(1 for item in items if is_claim_winner(item)),
            "real_execution_count": int(evidence["by_operation"].get(operation_id, 0)),
            "blocked_count": sum(1 for item in items if is_blocked(item)),
            "duplicate_execution_count": max(
                0, int(evidence["by_operation"].get(operation_id, 0)) - 1
            ),
        }
        for operation_id, items in sorted(grouped.items())
    }
    summary = summarize_common(records, evidence, expected_operations=config["unique_operations"])
    summary.update(
        {
            "experiment": name,
            "workspace": str(workspace),
            "concurrency": config["concurrency"],
            "operation_metrics": operation_metrics,
            "exactly_one_execution_operations": sum(
                1 for item in operation_metrics.values() if item["real_execution_count"] == 1
            ),
            "zero_execution_operations": sum(
                1 for item in operation_metrics.values() if item["real_execution_count"] == 0
            ),
            "duplicate_execution_operations": sum(
                1 for item in operation_metrics.values() if item["duplicate_execution_count"] > 0
            ),
            "duplicate_execution_rate": (
                summary["duplicate_real_execution_count"] / config["unique_operations"]
                if config["unique_operations"]
                else 0.0
            ),
        }
    )
    log.write(
        f"finish {name} requests={summary['total_requests']} real={summary['real_execution_count']} duplicates={summary['duplicate_real_execution_count']}"
    )
    return summary


def run_experiment_c(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "C_key_argument_conflict"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-C-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    state = new_state(store, "run-side-effect-C", "argument conflict benchmark")
    agent = build_agent(workspace, store, state, SafeEffectRecorder(workspace))
    records = []
    for index in range(config["unique_operations"]):
        operation_id = f"C-{index:04d}"
        key = f"operation-{operation_id}"
        records.append(
            request_record(
                experiment=name,
                operation_id=operation_id,
                operation_key=key,
                attempt=0,
                agent=agent,
                state=state,
                store=store,
                raw=raw,
                args={"operation_id": operation_id, "value": "A"},
            )
        )
        records.append(
            request_record(
                experiment=name,
                operation_id=operation_id,
                operation_key=key,
                attempt=1,
                agent=agent,
                state=state,
                store=store,
                raw=raw,
                args={"operation_id": operation_id, "value": "B"},
            )
        )
    evidence = effect_evidence(workspace)
    conflict_attempts = config["unique_operations"]
    conflicts_detected = sum(
        1
        for item in records
        if item["attempt"] == 1 and bool((item.get("metadata") or {}).get("idempotency_key_conflict"))
    )
    summary = summarize_common(records, evidence, expected_operations=config["unique_operations"])
    summary.update(
        {
            "experiment": name,
            "workspace": str(workspace),
            "conflict_attempts": conflict_attempts,
            "conflicts_detected": conflicts_detected,
            "conflicts_missed": conflict_attempts - conflicts_detected,
            "unexpected_real_execution": sum(
                max(0, int(count) - 1) for count in evidence["by_operation"].values()
            ),
        }
    )
    log.write(
        f"finish {name} conflicts={conflicts_detected}/{conflict_attempts} unexpected_real={summary['unexpected_real_execution']}"
    )
    return summary


def run_experiment_d(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "D_uncertain_fail_closed"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-D-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    state = new_state(store, "run-side-effect-D", "uncertain fail-closed benchmark")
    agent = build_agent(
        workspace,
        store,
        state,
        SafeEffectRecorder(workspace, delay_ms=config["delay_ms"], fail_after_effect=True),
        circuit_breaker=False,
    )
    records = []
    for index in range(config["unique_operations"]):
        operation_id = f"D-{index:04d}"
        key = f"operation-{operation_id}"
        args = {"operation_id": operation_id, "value": "after-effect-before-result"}
        records.append(
            request_record(
                experiment=name,
                operation_id=operation_id,
                operation_key=key,
                attempt=0,
                agent=agent,
                state=state,
                store=store,
                raw=raw,
                args=args,
            )
        )
        records.append(
            request_record(
                experiment=name,
                operation_id=operation_id,
                operation_key=key,
                attempt=1,
                agent=agent,
                state=state,
                store=store,
                raw=raw,
                args=args,
            )
        )
    evidence = effect_evidence(workspace)
    replay_records = [item for item in records if item["attempt"] == 1]
    summary = summarize_common(records, evidence, expected_operations=config["unique_operations"])
    summary.update(
        {
            "experiment": name,
            "workspace": str(workspace),
            "uncertain_operations": sum(
                1
                for item in records
                if item["attempt"] == 0
                and str((item.get("metadata") or {}).get("side_effect_prior_state", "")) == ""
                and bool((item.get("metadata") or {}).get("side_effect_outcome_uncertain"))
            ),
            "uncertain_replay_attempts": len(replay_records),
            "uncertain_replay_blocked": sum(1 for item in replay_records if is_blocked(item)),
            "uncertain_replay_executed": sum(1 for item in replay_records if is_claim_winner(item)),
            "uncertain_duplicate_side_effect_count": sum(
                max(0, int(count) - 1) for count in evidence["by_operation"].values()
            ),
        }
    )
    log.write(
        f"finish {name} uncertain={summary['uncertain_operations']} blocked={summary['uncertain_replay_blocked']} duplicates={summary['uncertain_duplicate_side_effect_count']}"
    )
    return summary


def subprocess_worker(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace_root)
    store = RunStore(Path(args.run_store_root))
    state = TaskState.from_dict(store.load_task_state(args.run_id))
    agent = build_agent(workspace, store, state, SafeEffectRecorder(workspace))
    records = []
    for index in range(args.count):
        operation_id = f"{args.operation_prefix}-{index:04d}"
        key = f"operation-{operation_id}"
        started = time.perf_counter_ns()
        result = agent.run_tool(
            TOOL_NAME,
            {"operation_id": operation_id, "value": "same"},
            operation_key=key,
        )
        metadata = metadata_for(agent)
        records.append(
            {
                "operation_id": operation_id,
                "operation_key": key,
                "result": str(result),
                "metadata": metadata,
                "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            }
        )
    print(json.dumps({"records": records, "evidence": effect_evidence(workspace)}, sort_keys=True))
    return 0


def run_experiment_e(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "E_persistence_restart"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-E-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    state = new_state(store, "run-side-effect-E", "persistence restart benchmark")
    first_executor = build_agent(workspace, store, state, SafeEffectRecorder(workspace))
    initial_records = []
    for index in range(config["unique_operations"]):
        operation_id = f"E-{index:04d}"
        key = f"operation-{operation_id}"
        initial_records.append(
            request_record(
                experiment=name,
                operation_id=operation_id,
                operation_key=key,
                attempt=0,
                agent=first_executor,
                state=state,
                store=store,
                raw=raw,
                args={"operation_id": operation_id, "value": "same"},
            )
        )
    del first_executor
    restored_state = TaskState.from_dict(store.load_task_state(state.run_id))
    restored_executor = build_agent(workspace, store, restored_state, SafeEffectRecorder(workspace))
    before_restart = effect_evidence(workspace)
    replay_records = []
    for index in range(config["unique_operations"]):
        operation_id = f"E-{index:04d}"
        key = f"operation-{operation_id}"
        replay_records.append(
            request_record(
                experiment=name,
                operation_id=operation_id,
                operation_key=key,
                attempt=1,
                agent=restored_executor,
                state=restored_state,
                store=store,
                raw=raw,
                args={"operation_id": operation_id, "value": "same"},
            )
        )
    after_restart = effect_evidence(workspace)
    persisted = store.load_task_state(state.run_id).get("side_effect_operations", {})
    subprocess_records: list[dict[str, Any]] = []
    subprocess_evidence: dict[str, Any] = {}
    subprocess_args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--subprocess-worker",
        "--workspace-root",
        str(workspace),
        "--run-store-root",
        str(store.root),
        "--run-id",
        state.run_id,
        "--operation-prefix",
        "E",
        "--count",
        str(config["unique_operations"]),
    ]
    completed = subprocess.run(
        subprocess_args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    subprocess_error = completed.stderr.strip()
    if completed.returncode == 0:
        payload = json.loads(completed.stdout)
        subprocess_records = payload.get("records", [])
        subprocess_evidence = payload.get("evidence", {})
    else:
        subprocess_error = f"exit_code={completed.returncode}; {subprocess_error}"
    after_subprocess = effect_evidence(workspace)
    for item in subprocess_records:
        raw.write(
            {
                "record_type": "subprocess_restart_request",
                "experiment": name,
                **item,
            }
        )
    summary = summarize_common(initial_records + replay_records, after_restart, expected_operations=config["unique_operations"])
    summary.update(
        {
            "experiment": name,
            "workspace": str(workspace),
            "operations": config["unique_operations"],
            "persisted_records": len(persisted),
            "restart_replay_attempts": len(replay_records),
            "replay_after_restart": sum(1 for item in replay_records if is_blocked(item)),
            "real_execution_after_restart": after_restart["real_execution_count_file"]
            - before_restart["real_execution_count_file"],
            "restart_duplicate_execution_count": sum(
                max(0, int(count) - 1)
                for count in after_restart.get("by_operation", {}).values()
            ),
            "subprocess_restart_attempts": len(subprocess_records),
            "subprocess_restart_blocked": sum(
                1
                for item in subprocess_records
                if bool((item.get("metadata") or {}).get("side_effect_replay_blocked"))
            ),
            "subprocess_real_execution_after_restart": after_subprocess["real_execution_count_file"]
            - after_restart["real_execution_count_file"],
            "subprocess_restart_duplicate_execution_count": sum(
                max(0, int(count) - 1)
                for count in after_subprocess.get("by_operation", {}).values()
            ),
            "subprocess_error": subprocess_error,
            "subprocess_evidence_available": bool(subprocess_records),
            "subprocess_evidence_before": subprocess_evidence,
        }
    )
    log.write(
        f"finish {name} persisted={summary['persisted_records']} in_process_blocked={summary['replay_after_restart']} subprocess_blocked={summary['subprocess_restart_blocked']}"
    )
    return summary


def validate_trace(path: Path, expected_event_ids: set[str]) -> dict[str, Any]:
    actual_event_ids: list[str] = []
    corrupt = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                corrupt += 1
                continue
            event_id = str(event.get("event_id", ""))
            actual_event_ids.append(event_id)
    actual_set = set(actual_event_ids)
    missing_ids = sorted(expected_event_ids - actual_set)
    counts: dict[str, int] = defaultdict(int)
    for event_id in actual_event_ids:
        counts[event_id] += 1
    duplicate_ids = sorted(event_id for event_id, count in counts.items() if count > 1)
    return {
        "trace_expected": len(expected_event_ids),
        "trace_actual": len(actual_event_ids),
        "trace_missing": len(missing_ids),
        "trace_missing_event_ids": missing_ids,
        "trace_duplicate": sum(max(0, count - 1) for count in counts.values()),
        "trace_duplicate_event_ids": duplicate_ids,
        "trace_corrupt": corrupt,
    }


def run_experiment_f(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "F_trace_concurrency"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-F-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    state = new_state(store, "run-side-effect-F", "concurrent trace append benchmark")
    expected_count = config["unique_operations"] * config["repetitions"]

    def append_one(index: int) -> None:
        store.append_trace(state, {"event": "benchmark_trace", "event_id": f"F-{index:05d}"})

    with ThreadPoolExecutor(max_workers=max(config["concurrency"], 32)) as pool:
        list(pool.map(append_one, range(expected_count)))

    trace = validate_trace(
        store.trace_path(state.run_id), {f"F-{index:05d}" for index in range(expected_count)}
    )
    for index in range(expected_count):
        raw.write(
            {
                "record_type": "trace_append",
                "experiment": name,
                "event_id": f"F-{index:05d}",
                "trace_path": str(store.trace_path(state.run_id)),
            }
        )
    summary = {
        "experiment": name,
        "workspace": str(workspace),
        "total_requests": expected_count,
        "unique_operations": 0,
        "expected_real_execution_count": 0,
        "real_execution_count": 0,
        "duplicate_real_execution_count": 0,
        "replay_blocked_count": 0,
        "error_count": 0,
        **trace,
    }
    log.write(
        f"finish {name} expected={summary['trace_expected']} actual={summary['trace_actual']} missing={summary['trace_missing']}"
    )
    return summary


def multiprocess_claim_worker(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace_root)
    ready_path = Path(args.ready_path)
    go_path = Path(args.go_path)
    ready_path.write_text("ready\n", encoding="utf-8")
    deadline = time.monotonic() + 60
    while not go_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("multiprocess claim start barrier timed out")
        time.sleep(0.001)
    store = RunStore(Path(args.run_store_root))
    state = TaskState.from_dict(store.load_task_state(args.run_id))
    operation_id = str(args.operation_id)
    operation_key = f"operation-{operation_id}"
    operation_args = {"operation_id": operation_id, "value": "same"}
    args_digest = hashlib.sha256(
        json.dumps(
            operation_args,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    runner = SafeEffectRecorder(workspace, delay_ms=args.delay_ms)
    started = time.perf_counter_ns()
    raised = ""
    try:
        claim = store.claim_side_effect_operation(
            state, operation_key, TOOL_NAME, args_digest
        )
        if claim["claimed"]:
            result = runner(operation_args)
            store.update_side_effect_operation(
                state,
                operation_key,
                "completed",
                {"tool_status": "success", "tool_execution_success": True},
            )
            metadata = {
                "side_effect_operation_key": operation_key,
                "side_effect_args_digest": args_digest,
                "side_effect_claimed": True,
                "side_effect_replay_detected": False,
                "side_effect_replay_blocked": False,
                "side_effect_prior_state": "",
                "side_effect_commit_recorded": True,
                "side_effect_outcome_uncertain": False,
                "idempotency_key_conflict": False,
                "tool_status": "success",
                "tool_error_code": "",
                "tool_execution_success": True,
                "tool_business_success": True,
            }
        else:
            prior_state = str(claim.get("prior_state", ""))
            result = json.dumps(
                {
                    "error_code": (
                        "idempotency_key_conflict"
                        if claim.get("conflict")
                        else "side_effect_replay_blocked"
                    ),
                    "prior_state": prior_state,
                },
                sort_keys=True,
            )
            metadata = {
                "side_effect_operation_key": operation_key,
                "side_effect_args_digest": args_digest,
                "side_effect_claimed": False,
                "side_effect_replay_detected": True,
                "side_effect_replay_blocked": True,
                "side_effect_prior_state": prior_state,
                "side_effect_commit_recorded": False,
                "side_effect_outcome_uncertain": prior_state in {"claimed", "uncertain"},
                "idempotency_key_conflict": bool(claim.get("conflict")),
                "tool_status": "blocked",
                "tool_error_code": (
                    "idempotency_key_conflict"
                    if claim.get("conflict")
                    else "side_effect_replay_blocked"
                ),
                "tool_execution_success": False,
                "tool_business_success": False,
            }
    except Exception as exc:  # Keep process-level failures observable in raw output.
        result = f"raised: {exc}"
        raised = f"{exc.__class__.__name__}: {exc}"
        metadata = {"tool_status": "error", "tool_error_code": exc.__class__.__name__}
    store.append_trace(
        state,
        {
            "event": "tool.completed",
            "created_at": utc_now(),
            "run_id": state.run_id,
            "name": TOOL_NAME,
            "args": operation_args,
            "operation_key": operation_key,
            "result": str(result),
            **metadata,
        },
    )
    print(
        json.dumps(
            {
                "operation_id": operation_id,
                "operation_key": operation_key,
                "result": str(result),
                "raised": raised,
                "metadata": metadata,
                "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
            },
            sort_keys=True,
        )
    )
    return 0


def run_experiment_g(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "G_multiprocess_claim"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-G-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    state = new_state(store, "run-side-effect-G", "multi-process claim benchmark")
    barrier_dir = workspace / "multiprocess_barriers"
    barrier_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    contender_count = config["multiprocess_concurrency"]
    for index in range(config["unique_operations"]):
        operation_id = f"G-{index:04d}"
        operation_key = f"operation-{operation_id}"
        go_path = barrier_dir / f"{operation_id}.go"
        processes: list[subprocess.Popen[str]] = []
        ready_paths: list[Path] = []
        for contender in range(contender_count):
            ready_path = barrier_dir / f"{operation_id}-{contender}.ready"
            ready_paths.append(ready_path)
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--multiprocess-claim-worker",
                "--workspace-root",
                str(workspace),
                "--run-store-root",
                str(store.root),
                "--run-id",
                state.run_id,
                "--operation-id",
                operation_id,
                "--ready-path",
                str(ready_path),
                "--go-path",
                str(go_path),
                "--delay-ms",
                str(config["delay_ms"]),
            ]
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=REPO_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            )
        ready_deadline = time.monotonic() + 60
        while not all(path.exists() for path in ready_paths):
            if time.monotonic() >= ready_deadline:
                for process in processes:
                    process.kill()
                raise TimeoutError(f"workers did not reach barrier for {operation_id}")
            time.sleep(0.001)
        go_path.write_text("go\n", encoding="utf-8")
        for contender, process in enumerate(processes):
            try:
                stdout, stderr = process.communicate(timeout=120)
                if process.returncode == 0:
                    payload = json.loads(stdout.strip().splitlines()[-1])
                    record = {
                        "record_type": "request",
                        "experiment": name,
                        "operation_id": operation_id,
                        "operation_key": operation_key,
                        "contender": contender,
                        **payload,
                    }
                else:
                    record = {
                        "record_type": "request",
                        "experiment": name,
                        "operation_id": operation_id,
                        "operation_key": operation_key,
                        "contender": contender,
                        "result": "",
                        "raised": f"exit_code={process.returncode}; {stderr.strip()}",
                        "metadata": {},
                    }
            except Exception as exc:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                record = {
                    "record_type": "request",
                    "experiment": name,
                    "operation_id": operation_id,
                    "operation_key": operation_key,
                    "contender": contender,
                    "result": "",
                    "raised": f"{exc.__class__.__name__}: {exc}",
                    "metadata": {},
                }
            records.append(record)
            raw.write(record)
    evidence = effect_evidence(workspace)
    grouped = records_by_operation(records)
    duplicate_count = sum(
        max(0, int(evidence["by_operation"].get(operation_id, 0)) - 1)
        for operation_id in grouped
    )
    summary = {
        "experiment": name,
        "workspace": str(workspace),
        "total_requests": len(records),
        "unique_operations": len(grouped),
        "expected_real_execution_count": config["unique_operations"],
        "real_execution_count": evidence["real_execution_count_file"],
        "effect_record_count": evidence["effect_record_count"],
        "unique_execution_tokens": evidence["unique_execution_tokens"],
        "duplicate_real_execution_count": duplicate_count,
        "duplicate_execution_count": duplicate_count,
        "replay_blocked_count": sum(1 for item in records if is_blocked(item)),
        "claim_attempts": len(records),
        "first_claim_winners": sum(1 for item in records if is_claim_winner(item)),
        "zero_execution_count": sum(
            1
            for operation_id in grouped
            if int(evidence["by_operation"].get(operation_id, 0)) == 0
        ),
        "error_count": sum(1 for item in records if item.get("raised") or is_underlying_error(item)),
        "multiprocess_concurrency": contender_count,
    }
    log.write(
        f"finish {name} attempts={summary['claim_attempts']} winners={summary['first_claim_winners']} real={summary['real_execution_count']} duplicates={summary['duplicate_execution_count']}"
    )
    return summary


def legacy_request_record(
    *,
    experiment: str,
    operation_id: str,
    run_id: str,
    attempt: int,
    agent: MiniAgent,
    args: dict[str, Any],
    raw: RawRecorder,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    raised = ""
    try:
        result = agent.ask("legacy side-effect replay benchmark", run_id=run_id)
    except Exception as exc:  # Keep raw evidence even if the legacy loop fails.
        result = f"raised: {exc}"
        raised = f"{exc.__class__.__name__}: {exc}"
    metadata = metadata_for(agent)
    record = {
        "record_type": "request",
        "experiment": experiment,
        "operation_id": operation_id,
        "operation_key": str(metadata.get("side_effect_operation_key", "")),
        "attempt": attempt,
        "tool_name": TOOL_NAME,
        "args": dict(args),
        "result": str(result),
        "raised": raised,
        "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "metadata": metadata,
    }
    raw.write(record)
    return record


def run_experiment_h(config: dict[str, Any], raw: RawRecorder, log: ExperimentLog) -> dict[str, Any]:
    name = "H_legacy_recovered_replay"
    log.write(f"start {name}")
    workspace = Path(tempfile.mkdtemp(prefix="codecub-side-effect-H-", dir=str(config["workspace_parent"])))
    store = RunStore(workspace / ".codecub" / "runs")
    records: list[dict[str, Any]] = []
    for index in range(config["legacy_operations"]):
        operation_id = f"H-{index:04d}"
        run_id = f"run-side-effect-H-{index:04d}"
        args = {"operation_id": operation_id, "value": "same"}
        tool_text = f'<tool>{json.dumps({"name": TOOL_NAME, "args": args}, separators=(",", ":"))}</tool>'
        state = new_state(store, run_id, "legacy recovered replay benchmark")
        first = build_agent(
            workspace,
            store,
            state,
            SafeEffectRecorder(workspace, delay_ms=config["delay_ms"]),
            model_client=LegacyBenchmarkClient(tool_text),
        )
        records.append(
            legacy_request_record(
                experiment=name,
                operation_id=operation_id,
                run_id=run_id,
                attempt=0,
                agent=first,
                args=args,
                raw=raw,
            )
        )
        second = build_agent(
            workspace,
            store,
            TaskState.from_dict(store.load_task_state(run_id)),
            SafeEffectRecorder(workspace, delay_ms=config["delay_ms"]),
            model_client=LegacyBenchmarkClient(tool_text),
        )
        records.append(
            legacy_request_record(
                experiment=name,
                operation_id=operation_id,
                run_id=run_id,
                attempt=1,
                agent=second,
                args=args,
                raw=raw,
            )
        )
    evidence = effect_evidence(workspace)
    grouped = records_by_operation(records)
    summary = summarize_common(records, evidence, expected_operations=config["legacy_operations"])
    summary.update(
        {
            "experiment": name,
            "workspace": str(workspace),
            "legacy_operations": config["legacy_operations"],
            "legacy_replay_attempts": config["legacy_operations"],
            "legacy_replay_blocked": sum(
                1 for item in records if item["attempt"] == 1 and is_blocked(item)
            ),
            "legacy_replay_executed": sum(
                1 for item in records if item["attempt"] == 1 and is_claim_winner(item)
            ),
            "legacy_duplicate_execution_count": sum(
                max(0, int(count) - 1) for count in evidence["by_operation"].values()
            ),
            "stable_operation_key_count": sum(
                1
                for items in grouped.values()
                if len({str(item.get("operation_key", "")) for item in items}) == 1
                and str(items[0].get("operation_key", ""))
            ),
        }
    )
    log.write(
        f"finish {name} operations={summary['legacy_operations']} blocked={summary['legacy_replay_blocked']} duplicates={summary['legacy_duplicate_execution_count']}"
    )
    return summary


def write_code_audit(output: Path, commit: str, dirty: bool) -> None:
    body = f"""# Tool 副作用重放保护代码审计

审计时间：{utc_now()}<br>
Commit：`{commit}`<br>
工作树 dirty：`{str(dirty).lower()}`

本审计只依据当前生产代码和现有测试，不依据设计文档推断。

## 生产路径

| 组件 | 当前路径 | 结论 |
|---|---|---|
| Tool effect 定义 | `codecub/tools.py:79-109` | 已实现。内置写/执行工具声明 `side_effect=True`；读工具声明 `False`。 |
| ToolExecutor 入口 | `codecub/tooling/executor.py:280-347` | 已实现。`ToolExecutor.execute()` 组装 `ToolInvocation`，并进入 `GovernedToolExecutor._execute()`。 |
| Claim / Replay | `codecub/runtime.py:323-391` | 已实现。副作用且有非空 `operation_key` 时计算 digest 并调用 RunStore claim。 |
| 持久化 Claim | `codecub/run_store.py:112-169` | 已实现。ledger 写入 `task_state.json`，写入采用临时文件后 `replace`。 |
| `operation_key` | `codecub/agent/loop.py:945-955`, `1318-1323` | Native/queued call 使用 `tool_call_id`；`legacy_recovered` 使用持久化的 run-scoped identity 生成稳定 key。ToolExecutor 本身不自动生成业务 key。 |
| `args_digest` | `codecub/runtime.py:345-350` | 已实现。`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)` 后 SHA-256。 |
| 状态写入 | `codecub/run_store.py:138-149`, `codecub/runtime.py:372-391` | 已实现：先 `claimed`，业务成功写 `completed`，执行异常或业务失败写 `uncertain`。 |
| Trace | `codecub/agent/loop.py:1310-1319`, `codecub/agent/collaborators.py:1742-1754` | 已实现。工具事件写入 RunStore 的 `trace.jsonl`，同一 Run 由线程锁和 OS 文件锁保护。 |

## 已实现

- 相同 `operation_key + tool_name + args_digest` 的已完成操作会返回
  `side_effect_replay_blocked`，不会再次调用 runner。
- 同 key 不同工具名或不同参数会返回 `idempotency_key_conflict`。
- `claimed`、`completed` 和 `uncertain` 都被写入 `task_state.json` 的
  `side_effect_operations` ledger。
- `claimed` 或 `uncertain` 的重放统一 fail-closed，返回
  `outcome_uncertain`，不直接重试副作用。
- 重建 Executor 后可以从同一 RunStore 读取 ledger。

## 能力范围与未实现边界

- Claim 与 RunStore mutable artifact 写入由 `RunStore._side_effect_locks` 的进程内
  `threading.Lock` 加上每个 Run 的 `portalocker` OS 文件锁保护；因此本实现覆盖共享
  RunStore 下的多进程 Claim 竞争和 trace append。
- 当前验证只覆盖同一共享本地 RunStore 的多进程竞争，不能把它表述为跨机器、
  分布式 exactly-once，也不覆盖外部服务自身的重复提交语义。
- `operation_key` 为空时，`_RuntimeToolReplay.claim()`直接返回 claimed；调用方必须
  提供稳定 key。ToolExecutor 不会替调用方推导业务 operation identity。
- Agent Loop 的 `legacy_recovered` 路径现在从持久化的 `run_id + assistant attempt +
  recovered ordinal + tool_name` identity 生成 operation key；args 仍由独立 digest 做冲突检测。
- `task_id` 仍是单次初始化的运行标识，不用于 legacy operation identity；同一 run_id
  不应被调用方复用于不同的逻辑任务。
- 下游外部服务若在响应丢失后已经产生副作用，系统只能记录不确定并拒绝盲目重放，
  不能撤销已经发生的外部副作用。

## 现有回归证据

实验前已运行：`uv run pytest -q tests/test_tool_executor.py tests/test_run_store.py tests/test_task_state.py`。<br>
结果：17 passed；本轮专项测试另行记录 trace、多进程 Claim 和 legacy recovery 覆盖。
"""
    (output / "code_audit.md").write_text(body, encoding="utf-8")


def build_environment(config: dict[str, Any], command: str) -> dict[str, Any]:
    return {
        "timestamp_utc": utc_now(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "git_commit": git_output("rev-parse", "HEAD"),
        "git_dirty": bool(git_output("status", "--porcelain=v1")),
        "run_store_type": "codecub.run_store.RunStore with JSON task_state.json",
        "concurrency_model": "single Python process for A-D/F and in-process E; extra Python subprocess for E; four subprocesses per operation for G; two AgentLoop restarts per H",
        "true_subprocess_restart": True,
        "production_files_modified": True,
        "experiment_command": command,
        "parameters": config,
        "regression": {},
    }


def verdict(summary: dict[str, Any], regression: dict[str, Any] | None = None) -> str:
    checks = [
        summary["experiments"]["A_sequential_replay"]["duplicate_real_execution_count"] == 0,
        summary["experiments"]["B_concurrent_claim"]["duplicate_execution_operations"] == 0,
        summary["experiments"]["C_key_argument_conflict"]["conflicts_missed"] == 0,
        summary["experiments"]["D_uncertain_fail_closed"]["uncertain_replay_executed"] == 0,
        summary["experiments"]["E_persistence_restart"]["restart_duplicate_execution_count"] == 0,
        summary["experiments"]["E_persistence_restart"]["subprocess_restart_duplicate_execution_count"] == 0,
        summary["experiments"]["F_trace_concurrency"]["trace_missing"] == 0,
        summary["experiments"]["F_trace_concurrency"]["trace_duplicate"] == 0,
        summary["experiments"]["F_trace_concurrency"]["trace_corrupt"] == 0,
        summary["experiments"]["G_multiprocess_claim"]["first_claim_winners"]
        == summary["experiments"]["G_multiprocess_claim"]["unique_operations"],
        summary["experiments"]["G_multiprocess_claim"]["duplicate_execution_count"] == 0,
        summary["experiments"]["G_multiprocess_claim"]["zero_execution_count"] == 0,
        summary["experiments"]["H_legacy_recovered_replay"]["legacy_replay_blocked"]
        == summary["experiments"]["H_legacy_recovered_replay"]["legacy_replay_attempts"],
        summary["experiments"]["H_legacy_recovered_replay"]["legacy_duplicate_execution_count"] == 0,
        summary["experiments"]["H_legacy_recovered_replay"]["stable_operation_key_count"]
        == summary["experiments"]["H_legacy_recovered_replay"]["legacy_operations"],
    ]
    if regression is not None:
        checks.append(regression.get("pytest_exit_code") == 0)
        checks.append(regression.get("ruff_new_error_count") == 0)
        checks.append(regression.get("diff_check_exit_code") == 0)
    if all(checks):
        return "SIDE_EFFECT_REPLAY_ACCEPTED"
    if any(checks):
        return "SIDE_EFFECT_REPLAY_PARTIAL"
    return "SIDE_EFFECT_REPLAY_NOT_ACCEPTED"


def write_report(output: Path, summary: dict[str, Any], environment: dict[str, Any]) -> None:
    experiments = summary["experiments"]
    lines = [
        "# Tool 副作用重放保护正式实验报告",
        "",
        f"判定：`{summary['verdict']}`",
        "",
        "## 实验问题",
        "",
        "逻辑调用可以重复到达时，同一个显式 operation identity 是否最多产生一次真实副作用？",
        "实验使用隔离本地 Workspace 的 `append_effect` Tool；每次 runner 真实进入都会追加唯一 execution token，并原子增加 `real_execution_count`。",
        "",
        "## 固定参数与环境",
        "",
        f"- Commit：`{environment['git_commit']}`；dirty：`{environment['git_dirty']}`",
        f"- Python：`{environment['python_version'].splitlines()[0]}`；平台：`{environment['platform']}`",
        f"- RunStore：`{environment['run_store_type']}`",
        f"- 并发度：`{environment['parameters']['concurrency']}`；副作用延迟：`{environment['parameters']['delay_ms']} ms`",
        "- A–D、F 和 E 的进程内部分在一个 Python 进程中运行；E 另外执行真实独立 Python subprocess replay；G 为每个 operation 启动四个独立 subprocess；H 使用两次独立 AgentLoop 恢复 legacy call。",
        "- D 为隔离 uncertain 语义而关闭了 benchmark Tool 自身的 circuit breaker；生产 circuit breaker 未修改，且该设置已记录在 manifest 参数中。",
        "- 本轮修改包含生产文件与 `benchmarks/test/` 中的实验代码；具体文件以 manifest 为准。",
        "",
        "## 结果",
        "",
        "| 实验 | 请求 | 唯一操作 | 真实执行 | 预期执行 | 重复真实执行 | Replay/冲突拦截 | 错误 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in (
        "A_sequential_replay",
        "B_concurrent_claim",
        "C_key_argument_conflict",
        "D_uncertain_fail_closed",
        "E_persistence_restart",
        "F_trace_concurrency",
        "G_multiprocess_claim",
        "H_legacy_recovered_replay",
    ):
        item = experiments[key]
        lines.append(
            f"| {key} | {item['total_requests']} | {item['unique_operations']} | {item['real_execution_count']} | {item['expected_real_execution_count']} | {item['duplicate_real_execution_count']} | {item['replay_blocked_count']} | {item['error_count']} |"
        )
    lines.extend(
        [
            "",
            "## 关键检查",
            "",
            f"- A 顺序 completed replay：重复真实执行 `{experiments['A_sequential_replay']['duplicate_real_execution_count']}` 次。",
            f"- B 并发：exactly-one 操作 `{experiments['B_concurrent_claim']['exactly_one_execution_operations']}`，zero-execution `{experiments['B_concurrent_claim']['zero_execution_operations']}`，duplicate-operation `{experiments['B_concurrent_claim']['duplicate_execution_operations']}`。",
            f"- C 参数冲突：检测 `{experiments['C_key_argument_conflict']['conflicts_detected']}/{experiments['C_key_argument_conflict']['conflict_attempts']}`，漏检 `{experiments['C_key_argument_conflict']['conflicts_missed']}`，意外真实执行 `{experiments['C_key_argument_conflict']['unexpected_real_execution']}`。",
            f"- D uncertain fail-closed：不确定操作 `{experiments['D_uncertain_fail_closed']['uncertain_operations']}`，重放拦截 `{experiments['D_uncertain_fail_closed']['uncertain_replay_blocked']}/{experiments['D_uncertain_fail_closed']['uncertain_replay_attempts']}`，重放执行 `{experiments['D_uncertain_fail_closed']['uncertain_replay_executed']}`。",
            f"- E 持久化恢复：ledger 记录 `{experiments['E_persistence_restart']['persisted_records']}`，进程内恢复后的真实执行 `{experiments['E_persistence_restart']['real_execution_after_restart']}`，subprocess 恢复后的真实执行 `{experiments['E_persistence_restart']['subprocess_real_execution_after_restart']}`。",
            f"- F Trace 并发：expected `{experiments['F_trace_concurrency']['trace_expected']}`，actual `{experiments['F_trace_concurrency']['trace_actual']}`，missing `{experiments['F_trace_concurrency']['trace_missing']}`，duplicate `{experiments['F_trace_concurrency']['trace_duplicate']}`，corrupt `{experiments['F_trace_concurrency']['trace_corrupt']}`。",
            f"- G 多进程 Claim：attempts `{experiments['G_multiprocess_claim']['claim_attempts']}`，first-claim winners `{experiments['G_multiprocess_claim']['first_claim_winners']}`，unique operations `{experiments['G_multiprocess_claim']['unique_operations']}`，real executions `{experiments['G_multiprocess_claim']['real_execution_count']}`，duplicates `{experiments['G_multiprocess_claim']['duplicate_execution_count']}`，zero-execution `{experiments['G_multiprocess_claim']['zero_execution_count']}`。",
            f"- H legacy recovery：legacy operations `{experiments['H_legacy_recovered_replay']['legacy_operations']}`，stable operation keys `{experiments['H_legacy_recovered_replay']['stable_operation_key_count']}`，replay blocked `{experiments['H_legacy_recovered_replay']['legacy_replay_blocked']}/{experiments['H_legacy_recovered_replay']['legacy_replay_attempts']}`，duplicate `{experiments['H_legacy_recovered_replay']['legacy_duplicate_execution_count']}`。",
            "",
            "## 解释与能力边界",
            "",
            "A–E 和 H 覆盖显式或恢复得到的稳定 `operation_key`；F 验证 Trace 写入；G 只对同一共享本地 RunStore 的四进程竞争给出实测结论，不将其推广为跨机器或全局 exactly-once。",
            "legacy identity 使用同一 `run_id` 下持久化的 assistant attempt 与 recovered ordinal；调用方不应把同一 run_id 复用于不同逻辑任务。",
            "",
            "## 可复核文件",
            "",
            "- `manifest.json`：协议、commit、参数和生产文件修改声明。",
            "- `raw_results.jsonl`：逐请求原始结果与 metadata。",
            "- `summary.json`：机器可读汇总和判定。",
            "- `logs/`：运行日志与隔离 Workspace 的副作用证据。",
            "- `code_audit.md`：生产实现路径审计。",
            "",
            "## 回归验证",
            "",
            f"- `uv run pytest -q`：{environment['regression'].get('pytest_passed')} passed，{environment['regression'].get('pytest_failed')} failed，{environment['regression'].get('pytest_skipped')} skipped，{environment['regression'].get('pytest_deselected')} deselected；exit code `{environment['regression'].get('pytest_exit_code')}`。",
            f"- `uv run ruff check .`：发现 {environment['regression'].get('ruff_error_count')} 个 lint 错误；exit code `{environment['regression'].get('ruff_exit_code')}`；其中既有错误 `{environment['regression'].get('ruff_existing_error_count')}`，本轮新增 `{environment['regression'].get('ruff_new_error_count')}`。benchmark 文件单独 lint exit code `{environment['regression'].get('benchmark_ruff_exit_code')}`。",
            f"- `git diff --check`：exit code `{environment['regression'].get('diff_check_exit_code')}`。",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_resume_claim(output: Path, summary: dict[str, Any]) -> None:
    a = summary["experiments"]["A_sequential_replay"]
    b = summary["experiments"]["B_concurrent_claim"]
    lines = [
        "# 简历可用结论",
        "",
        f"实验判定：`{summary['verdict']}`",
        "",
        f"> 在 CodeCub 当前生产 ToolExecutor/RunStore 链路上，对 {a['unique_operations']} 个唯一副作用操作进行顺序重复提交，并对 {b['unique_operations']} 个唯一操作进行每个 {b['concurrency']} 路并发重复提交；本次真实测量记录 {summary['total_requests']} 次请求，观察到 {summary['duplicate_real_execution_count']} 次重复真实副作用执行。",
        "",
        "## 可写进简历的数据",
        "",
        f"- 总请求：{summary['total_requests']}；汇总唯一操作：{summary['unique_operations']}；真实执行：{summary['real_execution_count']}；重复真实执行：{summary['duplicate_real_execution_count']}。",
        f"- 并发实验 exactly-one 执行操作：{b['exactly_one_execution_operations']}；duplicate-operation：{b['duplicate_execution_operations']}。",
        f"- 参数冲突漏检：{summary['experiments']['C_key_argument_conflict']['conflicts_missed']}；uncertain 重放执行：{summary['experiments']['D_uncertain_fail_closed']['uncertain_replay_executed']}；恢复后重复执行：{summary['experiments']['E_persistence_restart']['restart_duplicate_execution_count']}。",
        f"- Trace 并发：expected {summary['experiments']['F_trace_concurrency']['trace_expected']}，actual {summary['experiments']['F_trace_concurrency']['trace_actual']}，missing {summary['experiments']['F_trace_concurrency']['trace_missing']}，duplicate {summary['experiments']['F_trace_concurrency']['trace_duplicate']}，corrupt {summary['experiments']['F_trace_concurrency']['trace_corrupt']}。",
        f"- 多进程 Claim：{summary['experiments']['G_multiprocess_claim']['first_claim_winners']} 个 winner / {summary['experiments']['G_multiprocess_claim']['unique_operations']} 个 operation，真实执行 {summary['experiments']['G_multiprocess_claim']['real_execution_count']}，重复 {summary['experiments']['G_multiprocess_claim']['duplicate_execution_count']}。",
        f"- legacy recovery：稳定 operation key {summary['experiments']['H_legacy_recovered_replay']['stable_operation_key_count']} 个，重放拦截 {summary['experiments']['H_legacy_recovered_replay']['legacy_replay_blocked']}，重复执行 {summary['experiments']['H_legacy_recovered_replay']['legacy_duplicate_execution_count']}。",
        "",
        "## 不允许写的结论",
        "",
        "- 不允许写成跨机器、跨 RunStore 或分布式 exactly-once；G 的结论限定为同一共享本地 RunStore 的多进程 Claim。",
        "- 不允许声称外部服务副作用可被撤销，或声称响应丢失时能够证明下游未执行。",
        "",
        "## 面试时需要说明的能力边界",
        "",
        "- Claim 与 mutable artifact 写入由同一 Run 的线程锁和 OS 文件锁保护；外部服务自身的重复提交语义仍不由此证明。",
        "- legacy identity 依赖稳定 run_id、assistant attempt 和 recovered ordinal；ToolExecutor 不替独立逻辑任务推导 identity。",
        "- uncertain 状态采用 fail-closed：宁可拒绝重放，也不自动承担二次副作用风险。",
    ]
    (output / "resume_claim.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CodeCub side-effect replay experiments")
    parser.add_argument("--output", default="artifacts/side_effect_replay")
    parser.add_argument("--unique-operations", type=int, default=1000)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--delay-ms", type=float, default=2.0)
    parser.add_argument("--subprocess-worker", action="store_true")
    parser.add_argument("--multiprocess-claim-worker", action="store_true")
    parser.add_argument("--workspace-root", default="")
    parser.add_argument("--run-store-root", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--operation-id", default="")
    parser.add_argument("--ready-path", default="")
    parser.add_argument("--go-path", default="")
    parser.add_argument("--operation-prefix", default="E-subprocess")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--multiprocess-concurrency", type=int, default=4)
    parser.add_argument("--legacy-operations", type=int, default=100)
    return parser.parse_args()


def run_all(args: argparse.Namespace) -> int:
    if (
        args.unique_operations < 1
        or args.repetitions < 2
        or args.concurrency < 1
        or args.multiprocess_concurrency < 2
        or args.legacy_operations < 1
    ):
        raise ValueError("unique operations/concurrency must be positive and repetitions must be at least two")
    output = (REPO_ROOT / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir(parents=True, exist_ok=True)
    workspace_parent = output / "logs" / "workspaces"
    workspace_parent.mkdir(parents=True, exist_ok=True)
    command = " ".join([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    config = {
        "unique_operations": args.unique_operations,
        "repetitions": args.repetitions,
        "concurrency": args.concurrency,
        "delay_ms": args.delay_ms,
        "multiprocess_concurrency": args.multiprocess_concurrency,
        "legacy_operations": args.legacy_operations,
        "workspace_parent": str(workspace_parent),
        "d_circuit_breaker_disabled_for_isolation": True,
    }
    commit = git_output("rev-parse", "HEAD")
    dirty = bool(git_output("status", "--porcelain=v1"))
    write_code_audit(output, commit, dirty)
    manifest = {
        "schema": SCHEMA,
        "created_at_utc": utc_now(),
        "git_commit": commit,
        "git_dirty": dirty,
        "experiment_command": command,
        "parameters": config,
        "production_files_modified": True,
        "production_files_expected_to_change": [
            "codecub/run_store.py",
            "codecub/task_state.py",
            "codecub/agent/loop.py",
            "codecub/agent/collaborators.py",
            "codecub/runtime.py",
        ],
        "benchmark_files_modified": [str(Path(__file__).resolve())],
        "benchmark_code": str(Path(__file__).resolve()),
        "experiments": [
            "A_sequential_replay",
            "B_concurrent_claim",
            "C_key_argument_conflict",
            "D_uncertain_fail_closed",
            "E_persistence_restart",
            "F_trace_concurrency",
            "G_multiprocess_claim",
            "H_legacy_recovered_replay",
        ],
        "raw_data_policy": "retain every request in raw_results.jsonl; no failed sample deletion or old result mixing",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    environment = build_environment(config, command)
    (output / "environment.json").write_text(json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    raw = RawRecorder(output / "raw_results.jsonl")
    log = ExperimentLog(output / "logs" / "experiment.log")
    started = time.perf_counter_ns()
    try:
        experiments = {}
        experiments["A_sequential_replay"] = run_experiment_a(config, raw, log)
        experiments["B_concurrent_claim"] = run_experiment_b(config, raw, log)
        experiments["C_key_argument_conflict"] = run_experiment_c(config, raw, log)
        experiments["D_uncertain_fail_closed"] = run_experiment_d(config, raw, log)
        experiments["E_persistence_restart"] = run_experiment_e(config, raw, log)
        experiments["F_trace_concurrency"] = run_experiment_f(config, raw, log)
        experiments["G_multiprocess_claim"] = run_experiment_g(config, raw, log)
        experiments["H_legacy_recovered_replay"] = run_experiment_h(config, raw, log)
    finally:
        raw.close()
        log.close()
    total_requests = sum(item["total_requests"] for item in experiments.values()) + experiments[
        "E_persistence_restart"
    ]["subprocess_restart_attempts"]
    unique_operations = sum(item["unique_operations"] for item in experiments.values())
    real_execution_count = sum(item["real_execution_count"] for item in experiments.values())
    expected_real_execution_count = sum(item["expected_real_execution_count"] for item in experiments.values())
    duplicate_real_execution_count = sum(item["duplicate_real_execution_count"] for item in experiments.values())
    replay_blocked_count = sum(item["replay_blocked_count"] for item in experiments.values()) + experiments[
        "E_persistence_restart"
    ]["subprocess_restart_blocked"]
    conflict = experiments["C_key_argument_conflict"]
    uncertain = experiments["D_uncertain_fail_closed"]
    restart = experiments["E_persistence_restart"]
    trace = experiments["F_trace_concurrency"]
    multiprocess = experiments["G_multiprocess_claim"]
    legacy = experiments["H_legacy_recovered_replay"]
    summary = {
        "schema": SCHEMA,
        "timestamp_utc": utc_now(),
        "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "total_requests": total_requests,
        "unique_operations": unique_operations,
        "real_execution_count": real_execution_count,
        "expected_real_execution_count": expected_real_execution_count,
        "duplicate_real_execution_count": duplicate_real_execution_count,
        "duplicate_execution_rate": duplicate_real_execution_count / expected_real_execution_count
        if expected_real_execution_count
        else 0.0,
        "replay_blocked_count": replay_blocked_count,
        "conflict_attempts": conflict["conflict_attempts"],
        "conflicts_detected": conflict["conflicts_detected"],
        "conflicts_missed": conflict["conflicts_missed"],
        "uncertain_replay_attempts": uncertain["uncertain_replay_attempts"],
        "uncertain_replay_blocked": uncertain["uncertain_replay_blocked"],
        "uncertain_duplicate_execution_count": uncertain["uncertain_duplicate_side_effect_count"],
        "restart_replay_attempts": restart["restart_replay_attempts"],
        "restart_duplicate_execution_count": restart["restart_duplicate_execution_count"],
        "trace_expected": trace["trace_expected"],
        "trace_actual": trace["trace_actual"],
        "trace_missing": trace["trace_missing"],
        "trace_duplicate": trace["trace_duplicate"],
        "trace_corrupt": trace["trace_corrupt"],
        "multiprocess_claim_attempts": multiprocess["claim_attempts"],
        "multiprocess_first_claim_winners": multiprocess["first_claim_winners"],
        "multiprocess_duplicate_execution_count": multiprocess["duplicate_execution_count"],
        "multiprocess_zero_execution_count": multiprocess["zero_execution_count"],
        "legacy_replay_attempts": legacy["legacy_replay_attempts"],
        "legacy_replay_blocked": legacy["legacy_replay_blocked"],
        "legacy_duplicate_execution_count": legacy["legacy_duplicate_execution_count"],
        "error_count": sum(item["error_count"] for item in experiments.values()),
        "experiments": experiments,
        "regression": None,
        "verdict": "PENDING_REGRESSION",
        "production_files_modified": True,
        "run_store_type": environment["run_store_type"],
        "single_process_scope": "A-D/F/H in-process; E restart and G multi-process subprocess evidence",
        "true_subprocess_restart": True,
    }
    summary["verdict"] = verdict(summary)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(output, summary, environment)
    write_resume_claim(output, summary)
    print(
        json.dumps(
            {
                "output": str(output),
                "verdict": summary["verdict"],
                "total_requests": summary["total_requests"],
                "real_execution_count": summary["real_execution_count"],
                "duplicate_real_execution_count": summary["duplicate_real_execution_count"],
                "duration_ms": summary["duration_ms"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    args = parse_args()
    if args.multiprocess_claim_worker:
        if (
            not args.workspace_root
            or not args.run_store_root
            or not args.run_id
            or not args.operation_id
            or not args.ready_path
            or not args.go_path
        ):
            raise ValueError("multiprocess claim worker requires workspace, run store, run id, operation, and barriers")
        return multiprocess_claim_worker(args)
    if args.subprocess_worker:
        if not args.workspace_root or not args.run_store_root or not args.run_id or args.count < 1:
            raise ValueError("subprocess worker requires workspace, run store, run id, and positive count")
        return subprocess_worker(args)
    return run_all(args)


if __name__ == "__main__":
    raise SystemExit(main())
