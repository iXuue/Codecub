"""Run the repository's fixed deterministic Coding Agent regression suite."""

import json
from pathlib import Path

from codecub.evaluator import run_harness_regression_v2


def main():
    result = run_harness_regression_v2(
        artifact_path=Path("artifacts") / "regression-eval.json"
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    raise SystemExit(0 if result["summary"]["passed"] == result["summary"]["total_tasks"] else 1)


if __name__ == "__main__":
    main()
