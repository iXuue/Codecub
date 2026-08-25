"""Write a comparable A0–A8 ablation manifest before executing any variant."""

import argparse
import json

from codecub.experiments.ablations import write_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--task-set", default="formal-holdout")
    parser.add_argument("--step-budget", type=int, default=24)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--output", default="artifacts/experiments/ablation-manifest.json")
    args = parser.parse_args()
    rows = write_manifest(
        args.output,
        args.provider,
        args.model,
        args.task_set,
        args.step_budget,
        args.temperature,
        args.top_p,
    )
    print(json.dumps({"variants": len(rows), "output": args.output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
