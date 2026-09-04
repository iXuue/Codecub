"""Freeze a machine-readable Codecub Phase 0 baseline.

This runner only measures existing behaviour.  It intentionally does not
import the Spine or change Runtime configuration, so later phases can compare
their results against a stable provenance record.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINES = ROOT / "benchmarks" / "baselines"
BENCHMARK_VERSION = "codecub-phase0-v1"


def command_output(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def git_value(*args: str) -> str:
    result = command_output(["git", *args])
    return result["stdout"].strip() if result["returncode"] == 0 else ""


def write_json(name: str, payload: dict) -> None:
    (BASELINES / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def metadata() -> dict:
    return {
        "schema_version": 1,
        "benchmark_version": BENCHMARK_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_dirty": bool(git_value("status", "--porcelain")),
        "python": sys.version,
        "platform": platform.platform(),
        "provider": os.environ.get("CODECUB_PROVIDER", ""),
        "model": os.environ.get("CODECUB_MODEL", ""),
    }


def main() -> int:
    BASELINES.mkdir(parents=True, exist_ok=True)
    common = metadata()
    write_json("manifest.json", {**common, "completed": False, "stage": "pytest_collection"})
    collection = command_output([sys.executable, "-m", "pytest", "--collect-only", "-q"])
    write_json("manifest.json", {**common, "completed": False, "stage": "pytest"})
    tests = command_output([sys.executable, "-m", "pytest", "-q"])
    write_json("runtime.json", {**common, "pytest": tests, "collection": collection})
    write_json("manifest.json", {**common, "completed": False, "stage": "reliability"})
    reliability = command_output([sys.executable, "-m", "pytest", "-q", "tests/test_resilience.py"])
    write_json("reliability.json", {**common, "reliability": reliability})
    write_json("manifest.json", {**common, "completed": False, "stage": "retrieval"})

    retrieval = command_output([sys.executable, "-m", "pytest", "-q", "tests/test_hybrid_retrieval.py"])
    write_json(
        "retrieval.json",
        {
            **common,
            "deterministic_hybrid_retrieval": retrieval,
            "provider_backed_comparison": {
                "status": "not_run",
                "reason": "no explicit embedding/reranker provider configuration",
            },
        },
    )
    write_json("manifest.json", {**common, "completed": False, "stage": "regression"})
    regression = command_output([sys.executable, "scripts/eval_regression.py"])

    write_json("multi_agent.json", {**common, "status": "not_run", "reason": "requires a configured real provider"})
    write_json("context.json", {**common, "status": "not_run", "reason": "requires a configured real provider"})
    write_json("regression.json", {**common, "regression": regression})
    failures = [entry for entry in (tests, reliability, retrieval, regression) if entry["returncode"] != 0]
    write_json(
        "manifest.json",
        {
            **common,
            "completed": True,
            "commands": {
                "pytest": tests["returncode"],
                "reliability": reliability["returncode"],
                "retrieval": retrieval["returncode"],
                "regression": regression["returncode"],
            },
            "failure_count": len(failures),
        },
    )
    print(json.dumps({"baseline_dir": str(BASELINES), "failures": len(failures)}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
